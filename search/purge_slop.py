"""
Slop purge — re-screen everything already in the golden layer.

Re-runs the deterministic SlopDetector over every knowledge_items row and
deletes the hits (FTS rows follow via triggers), recording each rejection in
slop_rejects. Run once with --dry-run to inspect, then without to purge.

Usage:
    python -m search.purge_slop --dry-run
    python -m search.purge_slop
"""

import argparse
import json
import logging

from sqlalchemy import text

from .filters import SlopDetector
from .indexers.turso_indexer import TursoGoldenLayer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("capybara.purge")

SELECT_BATCH = 500


def purge_slop(dry_run: bool = False, limit: int = 0) -> dict:
    layer = TursoGoldenLayer()
    if not layer.engine:
        return {"error": "No database connection"}

    layer.create_tables()
    detector = SlopDetector()

    session = layer.Session()
    removed = 0
    kept = 0
    scanned = 0
    rejected: list = []

    try:
        offset = 0
        while True:
            rows = session.execute(
                text("""SELECT id, url, title, content_text, source_name
                        FROM knowledge_items ORDER BY id LIMIT :lim OFFSET :off"""),
                {"lim": SELECT_BATCH, "off": offset},
            ).fetchall()
            if not rows:
                break

            batch_removed = 0
            for row_id, url, title, content, source_name in rows:
                scanned += 1
                result = detector.analyze(
                    content or "", source_name or "", title or "", url or "",
                    {"author": "", "published_date": "", "content_text": content or ""},
                )
                if not result.is_slop:
                    kept += 1
                    continue

                rejected.append({"url": url, "score": result.confidence,
                                 "signals": result.signals[:10], "source": source_name})
                removed += 1
                if dry_run:
                    continue

                layer._record_slop_reject(url, result.confidence, result.signals,
                                          source_name, stage="purge")
                session.execute(
                    text("DELETE FROM content_simhash WHERE url = :url"), {"url": url}
                )
                session.execute(
                    text("DELETE FROM knowledge_items WHERE id = :id"), {"id": row_id}
                )
                session.commit()
                batch_removed += 1

            # Deletions shift later rows forward — don't skip them
            offset += len(rows) - batch_removed
            if limit and scanned >= limit:
                break

        if not dry_run and removed:
            layer.refresh_fts()

    finally:
        session.close()
        layer.close()

    stats = {"scanned": scanned, "removed": removed, "kept": kept, "dry_run": dry_run}
    logger.info(
        "Slop purge %s: scanned %d, %s %d, kept %d",
        "(dry run)" if dry_run else "complete", scanned,
        "would remove" if dry_run else "removed", removed, kept,
    )
    if dry_run and rejected:
        for entry in rejected[:20]:
            logger.info("  WOULD REMOVE %s (%.2f) — %s",
                        entry["url"], entry["score"], "; ".join(entry["signals"][:3]))
        if len(rejected) > 20:
            logger.info("  ... and %d more", len(rejected) - 20)
    return stats


def main():
    parser = argparse.ArgumentParser(description="Purge AI slop from the golden layer")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be removed without deleting")
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after scanning N items (0 = all)")
    args = parser.parse_args()
    stats = purge_slop(dry_run=args.dry_run, limit=args.limit)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
