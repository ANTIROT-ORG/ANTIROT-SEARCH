"""
Antirot Pipeline — Full data pipeline (classical IR only, no neural nets)
Crawl → Score → Filter → Index (Golden/Turso) + Archive (HuggingFace) + Cache (Redis)

Architecture layers:
  1. Crawling (Web Spider) - robots.txt, sitemap, conditional GET, change detection
  2. Content Processing - dedup, structured data, quality signals
  3. Human-content gate - multi-layer slop detection (algorithmic)
  4. Indexing - FTS5/BM25 + authority scoring
  5. Freshness - incremental crawl scheduling, change detection
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import glob
import logging
from typing import List, Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("capybara.pipeline")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def run_pipeline(category: str = "all", priority: int = 1,
                 incremental: bool = True) -> dict:
    """
    Run the full pipeline.
    If incremental=True, only crawl sources due for recrawl.
    Set CRAWL_SKIP_CRAWL=true to index pending data without crawling
    (used by the Common Crawl ingest workflow).
    """
    stats = {}
    total_start = time.time()
    skip_crawl = os.environ.get("CRAWL_SKIP_CRAWL", "false").lower() == "true"

    # ── Step 0: Check freshness schedule ──
    scheduler = None
    due_sources = None
    crawl_stats = {"total_pages": 0, "per_source": {}, "errors": []}

    if incremental and not skip_crawl:
        logger.info("═══ Step 0/5: Checking freshness schedule ═══")
        from .freshness import CrawlScheduler
        scheduler = CrawlScheduler()
        from .sources import SOURCE_MAP

        due = scheduler.get_due_sources()
        known_due = [name for name in due if name in SOURCE_MAP]
        has_schedule = bool(scheduler.schedule.get("sources"))

        if not has_schedule:
            # First run — crawl the default priority set
            due_sources = None
            logger.info("No crawl schedule yet — running initial priority crawl")
        elif known_due:
            due_sources = known_due
            logger.info(f"Sources due for recrawl: {len(due_sources)}")
        else:
            due_sources = []
            logger.info("No sources due for recrawl — crawling skipped")

        stats["freshness"] = scheduler.get_stats()
    elif skip_crawl:
        logger.info("═══ Step 0/5: Crawl skipped (CRAWL_SKIP_CRAWL=true) ═══")

    # ── Step 1: Crawl ──
    if due_sources != [] and not skip_crawl:
        logger.info("═══ Step 1/5: Crawling ═══")
        from .crawlers import KnowledgeCrawler
        crawler = KnowledgeCrawler(max_concurrent=5)
        try:
            crawl_stats = asyncio.run(
                crawler.run(category=category, priority=priority, source_names=due_sources)
            )
            stats["crawl"] = crawl_stats
            logger.info(
                f"Crawl: {crawl_stats['total_pages']} pages "
                f"from {crawl_stats['crawled_sources']} sources"
            )
        finally:
            asyncio.run(crawler.close())
    else:
        logger.info("═══ Step 1/5: Crawling skipped (nothing due) ═══")

    # ── Step 2: Content Processing (structured data + quality signals) ──
    logger.info("═══ Step 2/5: Content Processing ═══")
    from .content_processor import calculate_content_quality

    processed_count = 0

    for crawled_file in sorted(glob.glob(os.path.join(DATA_DIR, "crawled_*.jsonl"))):
        items = _load_jsonl(crawled_file)
        if not items:
            continue
        changed = False
        for item in items:
            if "quality_signals" not in item:
                item["quality_signals"] = calculate_content_quality(item)
                changed = True
        if changed:
            _save_jsonl(crawled_file, items)
            processed_count += len(items)

    logger.info(f"Processed {processed_count} new items with quality signals")

    # ── Step 3: Filter AI slop ──
    logger.info("═══ Step 3/5: Filtering AI Slop ═══")
    from .filters import SlopDetector
    detector = SlopDetector()

    total_human = 0
    total_slop = 0
    filtered_count = 0

    for crawled_file in sorted(glob.glob(os.path.join(DATA_DIR, "crawled_*.jsonl"))):
        filtered_file = crawled_file.replace("crawled_", "filtered_")

        # Re-filter when the crawl batch is newer than its filtered output
        if os.path.exists(filtered_file) and os.path.getmtime(filtered_file) >= os.path.getmtime(crawled_file):
            continue

        items = _load_jsonl(crawled_file)
        if not items:
            continue

        human, slop = detector.filter_content(items)
        total_human += len(human)
        total_slop += len(slop)
        filtered_count += 1
        _save_jsonl(filtered_file, human)

        if slop:
            _save_jsonl(crawled_file.replace("crawled_", "slop_"), slop)

    stats["filter"] = {
        "batches": filtered_count,
        "human": total_human,
        "slop": total_slop,
        "pass_rate": round(total_human / max(total_human + total_slop, 1) * 100, 1),
    }
    logger.info(
        f"Filter: {total_human} human, {total_slop} slop "
        f"({stats['filter']['pass_rate']}% pass) across {filtered_count} batches"
    )

    # ── Step 4: Index to Golden Layer (Turso/SQLite) ──
    logger.info("═══ Step 4/5: Golden Layer ═══")
    turso_result = _index_golden_turso()
    stats["turso"] = turso_result
    logger.info(f"Golden layer: {turso_result.get('total_items', 0)} items indexed")

    tantivy_result = _sync_tantivy_index()
    stats["tantivy"] = tantivy_result
    if tantivy_result.get("synced"):
        logger.info(f"Tantivy index synced: {tantivy_result.get('documents', 0)} documents")

    duckdb_result = _sync_duckdb_tier()
    stats["duckdb_fast_tier"] = duckdb_result
    if duckdb_result.get("synced"):
        logger.info(f"DuckDB/MotherDuck fast tier synced: {duckdb_result.get('items', 0)} items")

    # ── Step 5: Raw Layer (HuggingFace) ──
    logger.info("═══ Step 5/5: Raw Layer (HuggingFace) ═══")
    hf_result = _upload_raw_to_huggingface()
    stats["huggingface"] = hf_result
    logger.info(f"HuggingFace: {hf_result.get('total', 0)} items uploaded")

    # ── Bonus: Warm Redis cache ──
    logger.info("══ Bonus: Warming Redis cache ══")
    cache_result = _warm_redis_cache()
    stats["redis"] = cache_result

    # ── Record crawl schedule per source ──
    if scheduler and crawl_stats.get("per_source"):
        for source_name, s in crawl_stats["per_source"].items():
            try:
                scheduler.record_crawl(
                    source_name=source_name,
                    pages_crawled=s.get("pages", 0),
                    avg_quality=s.get("avg_quality", 0.5),
                    errors=s.get("errors", 0),
                )
            except Exception as e:
                logger.debug(f"Failed to record crawl for {source_name}: {e}")

    stats["total_duration_seconds"] = round(time.time() - total_start, 2)

    with open(os.path.join(DATA_DIR, "pipeline_stats.json"), "w") as f:
        json.dump(stats, f, indent=2, default=str)

    return stats


def _load_jsonl(path: str) -> List[Dict]:
    items = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
    return items


def _save_jsonl(path: str, items: List[Dict]):
    with open(path, "w") as f:
        for item in items:
            f.write(json.dumps(item, default=str) + "\n")


def _upload_raw_to_huggingface() -> Dict:
    """Upload all crawled data (raw) to HuggingFace datasets"""
    try:
        from huggingface_hub import HfApi
        token = os.environ.get("HF_TOKEN")
        repo = os.environ.get("HF_DATASET_REPO", "TheBoringRats/ratsearch-raw")

        if not token:
            return {"skipped": True, "reason": "HF_TOKEN not set"}

        api = HfApi(token=token)
        try:
            api.create_repo(repo_id=repo, repo_type="dataset", exist_ok=True)
        except Exception:
            pass

        import pandas as pd
        total = 0

        for pattern in ["crawled_*.jsonl", "filtered_*.jsonl"]:
            for f in sorted(glob.glob(os.path.join(DATA_DIR, pattern))):
                items = _load_jsonl(f)
                if not items:
                    continue

                df = pd.DataFrame(items)
                parquet_path = f.replace(".jsonl", ".parquet")
                df.to_parquet(parquet_path, index=False)

                filename = os.path.basename(parquet_path)
                api.upload_file(
                    path_or_fileobj=parquet_path,
                    path_in_repo=filename,
                    repo_id=repo,
                    repo_type="dataset",
                    commit_message=f"Update {filename}: {len(items)} items"
                )
                total += len(items)
                os.remove(parquet_path)

        stats_file = os.path.join(DATA_DIR, "crawl_stats.json")
        if os.path.exists(stats_file):
            api.upload_file(
                path_or_fileobj=stats_file,
                path_in_repo="crawl_stats.json",
                repo_id=repo,
                repo_type="dataset",
            )

        return {"total": total, "repo": repo}

    except ImportError:
        return {"skipped": True, "reason": "huggingface_hub not installed"}
    except Exception as e:
        return {"error": str(e)}


def _index_golden_turso() -> Dict:
    """Index human-content items into the Turso/SQLite golden layer"""
    from .indexers.turso_indexer import TursoGoldenLayer
    from .indexers.pagerank import apply_pagerank
    from .dedup import simhash64
    from .sources import SOURCE_MAP

    layer = TursoGoldenLayer()
    if not layer.engine:
        return {"error": "No Turso connection"}

    layer.create_tables()
    indexed = 0
    duplicates = 0

    for f in sorted(glob.glob(os.path.join(DATA_DIR, "filtered_*.jsonl"))):
        items = _load_jsonl(f)
        for item in items:
            # Near-duplicate gate (SimHash over content, threshold 7/64)
            fingerprint = simhash64((item.get("content_text") or "")[:4000])
            duplicate_of = layer.find_near_duplicate(fingerprint)
            if duplicate_of and duplicate_of != item.get("url"):
                duplicates += 1
                continue

            item_id = layer.upsert_knowledge_item(item)
            if item_id:
                layer.store_simhash(item["url"], fingerprint, item_id)
                layer.store_links(
                    item["url"],
                    (item.get("internal_links") or []) + (item.get("external_links") or []),
                )

                source_name = item.get("source_name", "")
                is_trusted = source_name in SOURCE_MAP
                layer.update_source_stats(
                    source_name,
                    item.get("quality_score", 0),
                    is_trusted,
                )
                indexed += 1

    layer.refresh_fts()
    pagerank_stats = apply_pagerank(layer)
    thesaurus_stats = _build_thesaurus(layer)
    stats = layer.get_stats()
    stats["indexed"] = indexed
    stats["duplicates_skipped"] = duplicates
    stats["pagerank"] = pagerank_stats
    stats["thesaurus"] = thesaurus_stats
    layer.close()
    logger.info(
        f"Golden layer: indexed {indexed}, skipped {duplicates} near-duplicates"
    )
    return stats


def _build_thesaurus(layer) -> Dict:
    """Rebuild the PPMI co-occurrence thesaurus from filtered content."""
    from .indexers.thesaurus import build_associations

    texts: List[str] = []
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "filtered_*.jsonl"))):
        for item in _load_jsonl(f):
            texts.append(
                f"{item.get('title', '')} {(item.get('content_text') or '')[:3000]}"
            )
            if len(texts) >= 5000:
                break
        if len(texts) >= 5000:
            break

    rows = build_associations(texts)
    stored = layer.replace_associations(rows)
    terms = len({row[0] for row in rows})
    logger.info(f"Thesaurus: {terms} terms, {stored} associations")
    return {"terms": terms, "associations": stored}


def _sync_tantivy_index() -> Dict:
    """Optionally rebuild the Tantivy serving index via the Rust binary."""
    binary = os.environ.get("RATSEARCH_TANTIVY_BIN", "")
    index_dir = os.environ.get("RATSEARCH_TANTIVY_INDEX", "")
    if not binary or not index_dir:
        return {"skipped": True, "reason": "RATSEARCH_TANTIVY_BIN/INDEX not set"}
    if not os.path.exists(binary):
        return {"skipped": True, "reason": f"binary not found: {binary}"}

    db_path = os.environ.get("RATSEARCH_DB_PATH", os.path.join(DATA_DIR, "local.db"))
    try:
        proc = subprocess.run(
            [binary, "sync", "--db", db_path, "--index", index_dir],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            return {"synced": False, "error": proc.stderr.strip()[:300]}
        try:
            parsed = json.loads(proc.stdout)
            return {"synced": True, "documents": parsed.get("documents", 0)}
        except json.JSONDecodeError:
            return {"synced": True}
    except Exception as e:
        return {"synced": False, "error": str(e)}


def _sync_duckdb_tier() -> Dict:
    """Sync top-ranked knowledge items to DuckDB/MotherDuck fast tier and export Parquet."""
    try:
        from .duckdb_tier import DuckDBFastTier
        tier = DuckDBFastTier()
        total = tier.sync_from_sqlite()
        parquet_path = tier.export_parquet()
        return {"synced": True, "items": total, "parquet_path": parquet_path, "is_motherduck": tier.is_motherduck}
    except Exception as e:
        logger.warning(f"DuckDB fast tier sync note: {e}")
        return {"synced": False, "error": str(e)}


def _warm_redis_cache() -> Dict:
    """Pre-cache popular queries and top results in Redis"""
    try:
        import redis
        r = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            db=0,
            decode_responses=True,
            socket_connect_timeout=3,
        )
        r.ping()

        from .indexers.turso_indexer import TursoGoldenLayer
        layer = TursoGoldenLayer()
        if layer.engine:
            stats = layer.get_stats()
            for cat in stats.get("categories", {}):
                results = layer.keyword_search("", category=cat, limit=10)
                cache_key = f"ratsearch:top:{cat}"
                r.setex(cache_key, 3600, json.dumps(results, default=str))

            r.setex("ratsearch:stats", 300, json.dumps(stats, default=str))
            layer.close()

        return {"warmed": True, "connected": True}

    except Exception as e:
        return {"warmed": False, "connected": False, "reason": str(e)}


# ============================================================
# CLI entry point
# ============================================================

if __name__ == "__main__":
    category = os.environ.get("CRAWL_CATEGORY", "all")
    priority = int(os.environ.get("CRAWL_PRIORITY", "1"))
    incremental = os.environ.get("CRAWL_INCREMENTAL", "true").lower() == "true"

    print("🕷️ Antirot Pipeline")
    print(f"   Category: {category} | Priority: ≤{priority} | Incremental: {incremental}")
    print()

    stats = run_pipeline(category=category, priority=priority, incremental=incremental)

    print(f"\n{'='*50}")
    print(f"Pipeline complete in {stats.get('total_duration_seconds', '?')}s")
    if "turso" in stats:
        print(f"Golden layer: {stats['turso'].get('total_items', 0)} items")
    if "huggingface" in stats:
        print(f"HuggingFace raw layer: {stats['huggingface'].get('total', 0)} items")
    if "filter" in stats:
        print(f"AI slop filtered: {stats['filter'].get('slop', 0)} items")
        print(f"Pass rate: {stats['filter'].get('pass_rate', 0)}%")
    if "redis" in stats:
        print(f"Redis cache: {'warmed' if stats['redis'].get('warmed') else 'skipped'}")

    # CI guard: a run that crawled nothing or could not index must fail loudly
    failures = []
    crawl = stats.get("crawl")
    if crawl is not None and int(crawl.get("total_pages") or 0) == 0:
        failures.append("crawl ran but fetched 0 pages")
    turso = stats.get("turso") or {}
    if turso.get("error"):
        failures.append(f"golden layer failed: {turso.get('error')}")
    duck = stats.get("duckdb_fast_tier") or {}
    if os.environ.get("MOTHERDUCK_TOKEN") and not duck.get("synced"):
        failures.append(f"MotherDuck fast tier failed: {duck.get('error')}")

    if failures:
        print("\nPIPELINE FAILED:")
        for reason in failures:
            print(f"  x {reason}")
        sys.exit(1)
    sys.exit(0)
