"""
DuckDB & MotherDuck Fast-Tier Integration
Provides high-performance Tier 1 fast-rank caching, columnar querying over Parquet,
and seamless MotherDuck cloud analytics.

Hierarchy:
  Tier 1: DuckDB / MotherDuck (top-ranked, sub-20ms instant retrieval)
  Tier 2: Turso / SQLite (FTS5 BM25, SimHash, PageRank link graph)
  Tier 3: Hugging Face Parquet datasets (columnar deep archive, read_parquet queries)
"""

import os
import json
import logging
import duckdb
from typing import List, Dict, Optional, Any

logger = logging.getLogger("capybara.duckdb_tier")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
FAST_DB_PATH = os.path.join(DATA_DIR, "fast_rank.duckdb")
PARQUET_PATH = os.path.join(DATA_DIR, "knowledge_items.parquet")


class DuckDBFastTier:
    """
    DuckDB / MotherDuck hybrid fast tier.
    Uses MotherDuck when MOTHERDUCK_TOKEN is present, otherwise high-performance local DuckDB.
    """

    def __init__(self, db_path: Optional[str] = None):
        md_token = os.environ.get("MOTHERDUCK_TOKEN")
        self.is_motherduck = bool(md_token)

        if self.is_motherduck:
            # Connect to MotherDuck cloud database
            db_target = "md:capybara_search"
            logger.info("Connecting to MotherDuck cloud tier...")
            self.con = duckdb.connect(db_target)
        else:
            self.db_path = db_path or FAST_DB_PATH
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self.con = duckdb.connect(self.db_path)
            # Enforce low-compute limits: avoid OOM and CPU core starvation on small instances
            try:
                self.con.execute("PRAGMA threads=2;")
                self.con.execute("PRAGMA max_memory='256MB';")
                self.con.execute("PRAGMA preserve_insertion_order=false;")
            except Exception:
                pass

        self._init_schema()

    def _init_schema(self):
        """Create the fast-rank table and indexing for instant top-result serving."""
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS fast_knowledge (
                id BIGINT,
                url VARCHAR PRIMARY KEY,
                title VARCHAR,
                source_name VARCHAR,
                source_category VARCHAR,
                content_snippet VARCHAR,
                author VARCHAR,
                published_date VARCHAR,
                quality_score DOUBLE,
                authority_score DOUBLE,
                freshness_score DOUBLE,
                overall_rank DOUBLE,
                media_json VARCHAR,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Index on overall_rank and source_category for instant sorting
        try:
            self.con.execute("CREATE INDEX IF NOT EXISTS idx_fast_rank ON fast_knowledge(overall_rank DESC)")
            self.con.execute("CREATE INDEX IF NOT EXISTS idx_fast_cat ON fast_knowledge(source_category)")
        except Exception:
            pass

    def sync_from_sqlite(self, sqlite_path: Optional[str] = None, top_k: int = 5000) -> int:
        """
        Sync top-ranked items from SQLite/Turso into the DuckDB fast-rank tier.
        Only items meeting quality thresholds are stored here for maximum serving speed.
        """
        sqlite_path = sqlite_path or os.path.join(DATA_DIR, "local.db")
        if not os.path.exists(sqlite_path):
            logger.warning(f"SQLite file not found for sync: {sqlite_path}")
            return 0

        # Install and load sqlite extension if needed
        try:
            self.con.execute("INSTALL sqlite; LOAD sqlite;")
        except Exception:
            pass

        # Query top-k items directly from SQLite using DuckDB
        query = f"""
            INSERT OR REPLACE INTO fast_knowledge
            SELECT 
                id, url, title, source_name, source_category,
                SUBSTRING(content_text, 1, 320) AS content_snippet,
                COALESCE(author, '') AS author,
                COALESCE(published_date, '') AS published_date,
                quality_score, authority_score, freshness_score, overall_rank,
                COALESCE(media_json, '') AS media_json,
                CURRENT_TIMESTAMP AS indexed_at
            FROM sqlite_scan('{sqlite_path}', 'knowledge_items')
            WHERE slop_verdict = 'pass' OR slop_verdict IS NULL
            ORDER BY overall_rank DESC
            LIMIT {top_k}
        """
        self.con.execute(query)
        count = self.con.execute("SELECT count(*) FROM fast_knowledge").fetchone()[0]
        logger.info(f"Fast tier synced with {count} top-ranked items")
        return count

    def export_parquet(self, output_path: Optional[str] = None) -> str:
        """
        Export full dataset to compressed Snappy Parquet for Hugging Face datasets.
        """
        output_path = output_path or PARQUET_PATH
        sqlite_path = os.path.join(DATA_DIR, "local.db")
        if not os.path.exists(sqlite_path):
            return ""

        try:
            self.con.execute("INSTALL sqlite; LOAD sqlite;")
            self.con.execute(f"""
                COPY (
                    SELECT * FROM sqlite_scan('{sqlite_path}', 'knowledge_items')
                ) TO '{output_path}' (FORMAT PARQUET, COMPRESSION 'SNAPPY')
            """)
            logger.info(f"Exported Parquet dataset to {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Parquet export failed: {e}")
            return ""

    def fast_search(self, query: str, limit: int = 10, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Instant Tier-1 search over pre-ranked knowledge items.
        Sub-10ms lookup suitable for instant page-load.
        """
        q_clean = query.strip().replace("'", "''")
        tokens = [t.lower().replace("'", "''") for t in q_clean.split() if len(t) > 1]

        if not tokens:
            # Browse top overall ranks if query is empty
            where_clause = ""
            if category:
                where_clause = f"WHERE source_category = '{category}'"
            sql = f"""
                SELECT id, url, title, source_name, source_category, content_snippet,
                       author, published_date, quality_score, authority_score,
                       freshness_score, overall_rank, media_json
                FROM fast_knowledge
                {where_clause}
                ORDER BY overall_rank DESC
                LIMIT {limit}
            """
        else:
            # Token match across title and snippet with overall_rank weighting
            match_clauses = []
            for t in tokens:
                match_clauses.append(f"(LOWER(title) LIKE '%{t}%' OR LOWER(content_snippet) LIKE '%{t}%')")
            combined_match = " AND ".join(match_clauses)

            cat_clause = f" AND source_category = '{category}'" if category else ""
            sql = f"""
                SELECT id, url, title, source_name, source_category, content_snippet,
                       author, published_date, quality_score, authority_score,
                       freshness_score, overall_rank, media_json,
                       (CASE WHEN LOWER(title) LIKE '%{tokens[0]}%' THEN 2.0 ELSE 1.0 END * overall_rank) AS score
                FROM fast_knowledge
                WHERE ({combined_match}){cat_clause}
                ORDER BY score DESC, overall_rank DESC
                LIMIT {limit}
            """

        try:
            rows = self.con.execute(sql).fetchall()
            results = []
            for r in rows:
                media = []
                if r[12]:
                    try:
                        media = json.loads(r[12])
                    except Exception:
                        pass
                results.append({
                    "id": r[0],
                    "url": r[1],
                    "title": r[2],
                    "source_name": r[3],
                    "source_category": r[4],
                    "snippet": r[5],
                    "author": r[6],
                    "published_date": r[7],
                    "quality_score": round(r[8] or 0.0, 3),
                    "authority_score": round(r[9] or 0.0, 3),
                    "freshness_score": round(r[10] or 0.0, 3),
                    "overall_rank": round(r[11] or 0.0, 3),
                    "media": media,
                    "tier": "fast_duckdb" if not self.is_motherduck else "fast_motherduck",
                })
            return results
        except Exception as e:
            logger.error(f"Fast search query error: {e}")
            return []

    def query_parquet_archive(self, parquet_uri_or_path: str, query: str, limit: int = 20, offset: int = 0) -> List[Dict]:
        """
        Tier-3: Run direct SQL over Parquet file or remote Hugging Face URL.
        Allows lazy-loading deeper historical pages without keeping them in RAM.
        """
        q_clean = query.strip().replace("'", "''")
        tokens = [t.lower().replace("'", "''") for t in q_clean.split() if len(t) > 1]
        token_clause = "1=1"
        if tokens:
            token_clause = " AND ".join([f"(LOWER(title) LIKE '%{t}%' OR LOWER(content_text) LIKE '%{t}%')" for t in tokens])

        sql = f"""
            SELECT id, url, title, source_name, source_category,
                   SUBSTRING(content_text, 1, 300) AS snippet,
                   author, published_date, quality_score, overall_rank
            FROM read_parquet('{parquet_uri_or_path}')
            WHERE {token_clause}
            ORDER BY overall_rank DESC
            LIMIT {limit} OFFSET {offset}
        """
        try:
            rows = self.con.execute(sql).fetchall()
            return [{
                "id": r[0], "url": r[1], "title": r[2], "source_name": r[3],
                "source_category": r[4], "snippet": r[5], "author": r[6],
                "published_date": r[7], "quality_score": r[8], "overall_rank": r[9],
                "tier": "parquet_archive",
            } for r in rows]
        except Exception as e:
            logger.error(f"Parquet query error: {e}")
            return []


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DuckDB / MotherDuck Tier CLI")
    parser.add_argument("action", choices=["sync", "search", "export"], help="Action to execute")
    parser.add_argument("--query", "-q", default="", help="Search query")
    parser.add_argument("--limit", "-l", type=int, default=10, help="Result limit")
    args = parser.parse_args()

    tier = DuckDBFastTier()
    if args.action == "sync":
        total = tier.sync_from_sqlite()
        print(f"✅ Synced {total} top-ranked items to DuckDB fast tier")
    elif args.action == "export":
        p = tier.export_parquet()
        print(f"✅ Exported Parquet to {p}")
    elif args.action == "search":
        res = tier.fast_search(args.query, limit=args.limit)
        print(f"Found {len(res)} results in fast tier:")
        for r in res:
            print(f" - [{r['overall_rank']}] {r['title']} ({r['url']})")
