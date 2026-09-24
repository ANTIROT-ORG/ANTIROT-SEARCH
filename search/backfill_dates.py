"""
Backfill published_date (and the freshness-dependent scores) for indexed rows
that have none.

Date sources, priority order:
1. crawled_*/filtered_* JSONL records: `published_date`, then
   `metadata.sitemap_lastmod`
2. `crawl_state.json`: HTTP `Last-Modified`

Never overwrites an existing date. `overall_rank` is nudged by the freshness
delta (0.15 weight) so the stored blend stays coherent.

Usage:
    python -m search.backfill_dates --dry-run
    python -m search.backfill_dates
"""

import argparse
import glob
import json
import logging
import os
from typing import Dict

from sqlalchemy import text

from .crawlers import DATA_DIR, normalize_date, freshness_for_date
from .indexers.turso_indexer import TursoGoldenLayer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("capybara.backfill_dates")

FRESHNESS_WEIGHT = 0.15  # freshness share of overall_rank (ContentScorer)


def collect_candidates() -> Dict[str, str]:
    """url → YYYY-MM-DD from every local artifact, best source first."""
    candidates: Dict[str, str] = {}

    # 1. JSONL records (crawl output carries page-declared dates + sitemap lastmod)
    patterns = ["crawled_*.jsonl", "filtered_*.jsonl", "crawled_cc_*.jsonl"]
    files = [f for pat in patterns for f in glob.glob(os.path.join(DATA_DIR, pat))]
    for path in files:
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    url = rec.get("url") or ""
                    if not url or url in candidates:
                        continue
                    date = normalize_date(rec.get("published_date")) or \
                        normalize_date((rec.get("metadata") or {}).get("sitemap_lastmod"))
                    if date:
                        candidates[url] = date
        except Exception as e:
            logger.warning(f"Skipping {path}: {e}")

    # 2. HTTP Last-Modified from conditional-GET state
    state_path = os.path.join(DATA_DIR, "crawl_state.json")
    try:
        state = json.load(open(state_path))
    except Exception:
        state = {}
    for url, meta in (state or {}).items():
        if url in candidates or not isinstance(meta, dict):
            continue
        date = normalize_date(meta.get("last_modified"))
        if date:
            candidates[url] = date

    return candidates


def backfill(dry_run: bool = False) -> dict:
    layer = TursoGoldenLayer()
    if not layer.engine:
        return {"error": "No database connection"}
    layer.create_tables()

    candidates = collect_candidates()
    session = layer.Session()
    updated = 0
    still_undated = 0

    try:
        rows = session.execute(
            text("""SELECT url, freshness_score FROM knowledge_items
                    WHERE published_date = '' OR published_date IS NULL""")
        ).fetchall()

        for url, old_freshness in rows:
            date = candidates.get(url)
            if not date:
                still_undated += 1
                continue
            if dry_run:
                updated += 1
                continue

            new_freshness = freshness_for_date(date)
            # Keep the stored blend coherent with the new freshness input
            session.execute(
                text("""UPDATE knowledge_items
                        SET published_date = :date,
                            freshness_score = :fresh,
                            overall_rank = MAX(0, MIN(1,
                                overall_rank + :delta))
                        WHERE url = :url
                          AND (published_date = '' OR published_date IS NULL)"""),
                {
                    "url": url,
                    "date": date,
                    "fresh": round(new_freshness, 4),
                    "delta": round(FRESHNESS_WEIGHT * (new_freshness - (old_freshness or 0.5)), 4),
                },
            )
            updated += 1

        if not dry_run and updated:
            session.commit()

        total = session.execute(text("SELECT COUNT(*) FROM knowledge_items")).scalar() or 0
        dated = session.execute(
            text("SELECT COUNT(*) FROM knowledge_items WHERE published_date != ''")
        ).scalar() or 0
    finally:
        session.close()
        layer.close()

    stats = {
        "dry_run": dry_run,
        "sources_found": len(candidates),
        "updated": updated,
        "still_undated": still_undated,
        "total_items": total,
        "dated_items": (dated + updated) if dry_run else dated,
    }
    logger.info(
        "Backfill %s: %d updated, %d still undated (dated in index: %d/%d, sources: %d)",
        "(dry run)" if dry_run else "complete", updated, still_undated,
        stats["dated_items"], total, len(candidates),
    )
    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill published dates into the index")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()
    print(json.dumps(backfill(dry_run=args.dry_run), indent=2))


if __name__ == "__main__":
    main()
