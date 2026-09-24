"""
Backups for the Antirot golden layer.

Two artifacts:
  1. Portable JSONL export (gzip) — works for local SQLite and remote Turso.
  2. SQLite file snapshot via VACUUM INTO — local database only, crash-safe.

Optional upload to a HuggingFace dataset repo with retention (keep last N).

CLI:
    python -m search.backup                     # export + snapshot + upload
    python -m search.backup --no-upload         # local artifacts only
    python -m search.backup --keep 14
    python -m search.backup --restore search/data/backup_20260920.jsonl.gz
"""

import argparse
import gzip
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("capybara.backup")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
BACKUP_PREFIX = "backup_"
DEFAULT_REPO = os.environ.get("BACKUP_REPO", "TheBoringRats/ratsearch-backups")
DEFAULT_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))

COLUMNS = [
    "url", "title", "source_name", "source_category", "content_text",
    "content_hash", "author", "published_date", "language", "word_count",
    "quality_score", "overall_rank", "authority_score", "freshness_score",
    "engagement_score", "media_json",
]


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def export_jsonl(out_path: Optional[str] = None, batch_size: int = 1000) -> Dict:
    """Stream the golden layer to a gzipped JSONL file (portable backup)."""
    from sqlalchemy import text
    from .indexers.turso_indexer import TursoGoldenLayer

    out_path = out_path or os.path.join(DATA_DIR, f"{BACKUP_PREFIX}{_timestamp()}.jsonl.gz")
    layer = TursoGoldenLayer()
    if not layer.engine:
        return {"error": "no database connection"}

    session = layer.Session()
    count = 0
    try:
        result = session.execute(text(f"SELECT {', '.join(COLUMNS)} FROM knowledge_items"))
        with gzip.open(out_path, "wt", encoding="utf-8") as fh:
            while True:
                rows = result.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    record = dict(zip(COLUMNS, row))
                    fh.write(json.dumps(record, default=str) + "\n")
                    count += 1
    finally:
        session.close()
        layer.close()

    return {"path": out_path, "items": count, "bytes": os.path.getsize(out_path)}


def snapshot_sqlite(db_path: Optional[str] = None) -> Optional[Dict]:
    """Crash-safe local snapshot using VACUUM INTO. Skipped for remote Turso."""
    if os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN"):
        return None  # remote — JSONL export is the backup

    db_path = db_path or os.path.join(DATA_DIR, "local.db")
    if not os.path.exists(db_path):
        return None

    out_dir = os.path.dirname(db_path)
    out_path = os.path.join(out_dir, f"snapshot_{_timestamp()}.db")
    escaped = out_path.replace("'", "''")

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(f"VACUUM INTO '{escaped}'")
    finally:
        connection.close()

    return {"path": out_path, "bytes": os.path.getsize(out_path)}


def select_prune(files: List[str], keep: int) -> List[str]:
    """Return backup files to delete, oldest first (timestamped names)."""
    backups = sorted(f for f in files if os.path.basename(f).startswith(BACKUP_PREFIX))
    if keep <= 0:
        return backups
    return backups[:-keep] if len(backups) > keep else []


def upload_backup(path: str, repo: str = DEFAULT_REPO, keep: int = DEFAULT_KEEP) -> Dict:
    """Upload a backup to HuggingFace and prune old copies."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        return {"skipped": True, "reason": "HF_TOKEN not set"}

    try:
        from huggingface_hub import HfApi
        api = HfApi(token=token)
        try:
            api.create_repo(repo_id=repo, repo_type="dataset", exist_ok=True)
        except Exception:
            pass

        filename = os.path.basename(path)
        api.upload_file(
            path_or_fileobj=path,
            path_in_repo=filename,
            repo_id=repo,
            repo_type="dataset",
            commit_message=f"Backup {filename}",
        )

        files = api.list_repo_files(repo_id=repo, repo_type="dataset")
        to_delete = select_prune(files, keep)
        for stale in to_delete:
            try:
                api.delete_file(path_in_repo=stale, repo_id=repo, repo_type="dataset")
            except Exception as e:
                logger.warning(f"Could not delete {stale}: {e}")

        return {"uploaded": filename, "repo": repo, "pruned": len(to_delete)}
    except Exception as e:
        return {"error": str(e)}


def restore_jsonl(path: str) -> Dict:
    """Restore knowledge_items from a JSONL backup (upsert by URL)."""
    from .indexers.turso_indexer import TursoGoldenLayer

    if not os.path.exists(path):
        return {"error": f"file not found: {path}"}

    layer = TursoGoldenLayer()
    if not layer.engine:
        return {"error": "no database connection"}
    layer.create_tables()

    restored = 0
    skipped = 0
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if layer.upsert_knowledge_item(record):
                    restored += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1

    layer.refresh_fts()
    layer.close()
    return {"restored": restored, "skipped": skipped, "source": path}


def run_backup(db_path: Optional[str] = None, out_path: Optional[str] = None,
               repo: str = DEFAULT_REPO, keep: int = DEFAULT_KEEP,
               upload: bool = True) -> Dict:
    started = time.time()
    stats: Dict = {"timestamp": _timestamp()}

    export = export_jsonl(out_path)
    stats["export"] = export
    logger.info(f"Exported {export.get('items', 0)} items to {export.get('path')}")

    snapshot = snapshot_sqlite(db_path)
    if snapshot:
        stats["snapshot"] = snapshot
        logger.info(f"SQLite snapshot: {snapshot['path']}")

    if upload and "path" in export:
        stats["upload"] = upload_backup(export["path"], repo=repo, keep=keep)
        logger.info(f"Upload: {stats['upload']}")

    stats["duration_seconds"] = round(time.time() - started, 2)
    return stats


def main():
    parser = argparse.ArgumentParser(description="Antirot golden-layer backups")
    parser.add_argument("--db", default=None, help="SQLite path (default search/data/local.db)")
    parser.add_argument("--out", default=None, help="JSONL output path")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="HuggingFace dataset repo")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="remote copies to keep")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--restore", default=None, help="restore from a JSONL(.gz) backup")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.restore:
        print(json.dumps(restore_jsonl(args.restore), indent=2))
        return

    stats = run_backup(
        db_path=args.db,
        out_path=args.out,
        repo=args.repo,
        keep=args.keep,
        upload=not args.no_upload,
    )
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
