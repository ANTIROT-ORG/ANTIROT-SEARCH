"""
Turso Golden Layer — Production Indexer (classical IR only)

Schema: knowledge_items + FTS5 virtual table + source_stats.
Ranking: FTS5 BM25 (field-weighted) blended with authority/freshness.
No embeddings, no neural models — text and statistics only.
"""

import os
import json
import logging
import re
from typing import List, Dict, Optional, Tuple
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger("capybara.turso")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# FTS5 BM25 field weights: title, source_name, content_text, author
BM25_WEIGHTS = (3.0, 1.5, 1.0, 0.5)

# Trigger names kept in sync with web/src/lib/turso.ts
FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS knowledge_items_ai AFTER INSERT ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(rowid, title, source_name, content_text, author)
    VALUES (new.id, new.title, new.source_name, new.content_text, COALESCE(new.author, ''));
END;
CREATE TRIGGER IF NOT EXISTS knowledge_items_ad AFTER DELETE ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, source_name, content_text, author)
    VALUES ('delete', old.id, old.title, old.source_name, old.content_text, COALESCE(old.author, ''));
END;
CREATE TRIGGER IF NOT EXISTS knowledge_items_au AFTER UPDATE ON knowledge_items BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, source_name, content_text, author)
    VALUES ('delete', old.id, old.title, old.source_name, old.content_text, COALESCE(old.author, ''));
    INSERT INTO knowledge_fts(rowid, title, source_name, content_text, author)
    VALUES (new.id, new.title, new.source_name, new.content_text, COALESCE(new.author, ''));
END;
"""


def clean_for_search(text_val: str) -> str:
    """Strip HTML tags, normalize whitespace, lowercase for search indexing"""
    if not text_val:
        return ""
    text_val = re.sub(r'<[^>]+>', '', text_val)
    text_val = re.sub(r'\s+', ' ', text_val)
    return text_val.strip().lower()[:5000]


def validate_item(item: Dict) -> Optional[Dict]:
    """Validate and clean a knowledge item before insertion"""
    url = (item.get("url") or "").strip()
    title = (item.get("title") or "").strip()
    content = (item.get("content_text") or "").strip()

    if not url or not title or not content:
        return None

    # Sanitize strings for SQLite
    for key in ("title", "content_text", "author", "published_date", "source_name",
                "source_category", "content_hash", "language", "media_json"):
        val = item.get(key)
        if val is not None and not isinstance(val, str):
            if key == "media_json":
                val = json.dumps(val, default=str)
            else:
                val = str(val)
            item[key] = val
        if val is not None:
            item[key] = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(val))

    # Ensure numeric fields
    item["word_count"] = max(0, int(item.get("word_count") or 0))
    item["quality_score"] = max(0.0, min(1.0, float(item.get("quality_score") or 0.0)))
    item["overall_rank"] = max(0.0, min(1.0, float(item.get("overall_rank", item["quality_score"]) or 0.0)))
    item["authority_score"] = max(0.0, min(1.0, float(item.get("authority_score") or 0.0)))
    item["freshness_score"] = max(0.0, min(1.0, float(item.get("freshness_score") or 0.0)))
    item["engagement_score"] = max(0.0, min(1.0, float(item.get("engagement_score") or 0.0)))

    # Media discovery lives on the crawl record — persist it for the images tab
    if not item.get("media_json") and item.get("media"):
        item["media_json"] = json.dumps(item["media"], default=str)

    # Truncate content to 50000 chars for storage
    if len(item["content_text"]) > 50000:
        item["content_text"] = item["content_text"][:50000].rsplit(" ", 1)[0]

    return item


class TursoGoldenLayer:
    """
    Manages the golden layer in Turso (or local SQLite).
    """

    def __init__(self, database_url: Optional[str] = None, auth_token: Optional[str] = None):
        self.database_url = database_url or os.environ.get("TURSO_DATABASE_URL", "")
        self.auth_token = auth_token or os.environ.get("TURSO_AUTH_TOKEN", "")
        self.engine = None
        self.Session = None

        if self.database_url and self.auth_token:
            self._connect()
        else:
            # Local SQLite fallback — same file the Astro app reads
            local_db = os.path.join(DATA_DIR, "local.db")
            os.makedirs(os.path.dirname(local_db), exist_ok=True)
            self.engine = create_engine(f"sqlite:///{local_db}", echo=False)
            self.Session = sessionmaker(bind=self.engine)
            logger.info(f"Using local SQLite: {local_db}")

    def _connect(self):
        try:
            url = self.database_url
            if url.startswith("libsql://"):
                url = f"sqlite+libsql://{url.replace('libsql://', '')}"
            elif not url.startswith("sqlite+"):
                url = f"sqlite+libsql://{url}"

            self.engine = create_engine(
                url,
                connect_args={"auth_token": self.auth_token, "check_same_thread": False},
                pool_pre_ping=True,
                pool_recycle=3600,
            )
            self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
            logger.info("Connected to Turso golden layer")
        except Exception as e:
            logger.error(f"Failed to connect to Turso: {e}")
            raise

    def _column_names(self, conn, table: str = "knowledge_items") -> set:
        try:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return {r[1] for r in rows}
        except Exception:
            return set()

    def create_tables(self):
        if not self.engine:
            return

        with self.engine.connect() as conn:
            # Low-compute SQLite pragmas: zero-copy mmap, WAL, and capped page cache
            try:
                conn.execute(text("PRAGMA journal_mode = WAL;"))
                conn.execute(text("PRAGMA synchronous = NORMAL;"))
                conn.execute(text("PRAGMA mmap_size = 268435456;"))
                conn.execute(text("PRAGMA temp_store = MEMORY;"))
                conn.execute(text("PRAGMA cache_size = -32000;"))
            except Exception:
                pass

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    source_name TEXT NOT NULL DEFAULT '',
                    source_category TEXT NOT NULL DEFAULT '',
                    content_text TEXT NOT NULL DEFAULT '',
                    content_hash TEXT,
                    author TEXT DEFAULT '',
                    published_date TEXT DEFAULT '',
                    language TEXT DEFAULT 'en',
                    word_count INTEGER DEFAULT 0,
                    quality_score REAL DEFAULT 0.0,
                    overall_rank REAL DEFAULT 0.0,
                    authority_score REAL DEFAULT 0.0,
                    freshness_score REAL DEFAULT 0.0,
                    engagement_score REAL DEFAULT 0.0,
                    media_json TEXT DEFAULT '',
                    slop_score REAL DEFAULT 0.0,
                    slop_verdict TEXT DEFAULT 'pass',
                    slop_signals TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # Migrate older databases missing newer columns
            existing = self._column_names(conn)
            for column, ddl in (
                ("authority_score", "REAL DEFAULT 0.0"),
                ("freshness_score", "REAL DEFAULT 0.0"),
                ("engagement_score", "REAL DEFAULT 0.0"),
                ("media_json", "TEXT DEFAULT ''"),
                ("slop_score", "REAL DEFAULT 0.0"),
                ("slop_verdict", "TEXT DEFAULT 'pass'"),
                ("slop_signals", "TEXT DEFAULT ''"),
            ):
                if existing and column not in existing:
                    try:
                        conn.execute(text(f"ALTER TABLE knowledge_items ADD COLUMN {column} {ddl}"))
                        logger.info(f"Migrated knowledge_items: added {column}")
                    except Exception as e:
                        logger.warning(f"Migration note ({column}): {e}")

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS source_stats (
                    source_name TEXT PRIMARY KEY,
                    total_items INTEGER DEFAULT 0,
                    last_crawl TEXT DEFAULT '',
                    avg_quality REAL DEFAULT 0.0,
                    is_trusted INTEGER DEFAULT 0
                )
            """))

            # Slop gate audit trail — every rejection, at every stage
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS slop_rejects (
                    url TEXT PRIMARY KEY,
                    score REAL DEFAULT 0.0,
                    signals TEXT DEFAULT '',
                    source_name TEXT DEFAULT '',
                    stage TEXT DEFAULT 'index',
                    detected_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # Knowledge panel entity cache (Wikidata claims, 7-day TTL)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS knowledge_entities (
                    qid TEXT PRIMARY KEY,
                    label TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    entity_type TEXT DEFAULT '',
                    props_json TEXT DEFAULT '',
                    image_url TEXT DEFAULT '',
                    wiki_url TEXT DEFAULT '',
                    query_key TEXT DEFAULT '',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_entities_query ON knowledge_entities(query_key)"))

            # Near-duplicate fingerprints (SimHash banded: 8 × 8-bit)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS content_simhash (
                    url TEXT PRIMARY KEY,
                    simhash TEXT NOT NULL,
                    band0 INTEGER, band1 INTEGER, band2 INTEGER, band3 INTEGER,
                    band4 INTEGER, band5 INTEGER, band6 INTEGER, band7 INTEGER,
                    item_id INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """))
            existing_simhash = self._column_names(conn, "content_simhash")
            for band in range(8):
                if existing_simhash and f"band{band}" not in existing_simhash:
                    try:
                        conn.execute(text(f"ALTER TABLE content_simhash ADD COLUMN band{band} INTEGER"))
                    except Exception:
                        pass
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_simhash_b{band} ON content_simhash(band{band})"))

            # Link graph for PageRank
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS page_links (
                    src_url TEXT NOT NULL,
                    dst_url TEXT NOT NULL
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_links_src ON page_links(src_url)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_links_dst ON page_links(dst_url)"))

            # PPMI co-occurrence thesaurus
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS term_associations (
                    term TEXT NOT NULL,
                    related TEXT NOT NULL,
                    ppmi REAL NOT NULL,
                    cooc INTEGER NOT NULL,
                    PRIMARY KEY (term, related)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_assoc_term ON term_associations(term)"))

            # Privacy-preserving query stats (normalized query text only)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS query_stats (
                    query TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 1,
                    last_seen TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # Indexes
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ki_category ON knowledge_items(source_category)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ki_source ON knowledge_items(source_name)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ki_hash ON knowledge_items(content_hash)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ki_quality ON knowledge_items(quality_score DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ki_rank ON knowledge_items(overall_rank DESC)"))

            # FTS5 for fast keyword search (external content + sync triggers)
            try:
                conn.execute(text("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                        title, source_name, content_text, author,
                        content='knowledge_items',
                        content_rowid='id',
                        tokenize='porter unicode61'
                    )
                """))
                for trigger in FTS_TRIGGERS.strip().split("END;"):
                    stmt = trigger.strip()
                    if stmt:
                        conn.execute(text(stmt + "END;"))
            except Exception as e:
                logger.warning(f"FTS5 creation note: {e}")

            conn.commit()
            logger.info("Golden layer tables created")

    def _rebuild_fts(self):
        """Rebuild FTS index from knowledge_items"""
        if not self.engine:
            return
        with self.engine.connect() as conn:
            try:
                conn.execute(text("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')"))
                conn.commit()
                logger.info("FTS index rebuilt")
            except Exception as e:
                logger.error(f"FTS rebuild error: {e}")

    def _record_slop_reject(self, url: str, score: float, signals, source_name: str, stage: str = "index"):
        """Audit-trail every slop rejection (never raises into the caller)."""
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text("""INSERT INTO slop_rejects (url, score, signals, source_name, stage)
                            VALUES (:url, :score, :signals, :source, :stage)
                            ON CONFLICT(url) DO UPDATE SET
                                score=:score, signals=:signals,
                                source_name=:source, stage=:stage,
                                detected_at=CURRENT_TIMESTAMP"""),
                    {
                        "url": url,
                        "score": round(float(score), 4),
                        "signals": json.dumps([str(s) for s in (signals or [])][:25]),
                        "source": source_name or "",
                        "stage": stage,
                    },
                )
        except Exception as e:
            logger.warning(f"Slop reject audit failed for {url}: {e}")

    @staticmethod
    def _slop_gate(item: Dict) -> Tuple[bool, float, List[str]]:
        """Last line of defence: decide whether this item may be indexed.

        Uses the verdict carried from pipeline step 3 when present, otherwise
        runs the deterministic detector inline — so no ingest path (pipeline,
        wiki crawlers, CC/Wiki dumps, backup restore) can bypass the gate.
        """
        carried = item.get("_slop_verdict")
        if carried is None and "_slop_score" in item:
            # Score carried without an explicit verdict: a positive score on an
            # accepted item is its confidence, not a rejection.
            carried = "pass"
        if carried == "rejected":
            return True, float(item.get("_slop_score") or 0.0), list(item.get("_slop_signals") or [])
        if carried == "pass":
            return False, float(item.get("_slop_score") or 0.0), list(item.get("_slop_signals") or [])

        from ..filters import SlopDetector

        result = SlopDetector().analyze(
            item.get("content_text") or "",
            item.get("source_name") or "",
            item.get("title") or "",
            item.get("url") or "",
            item,
        )
        return result.is_slop, result.confidence, result.signals

    def upsert_knowledge_item(self, item: Dict) -> Optional[int]:
        item = validate_item(item)
        if not item:
            return None

        is_slop, slop_score, slop_signals = self._slop_gate(item)
        if is_slop:
            self._record_slop_reject(
                item["url"], slop_score, slop_signals,
                item.get("source_name", ""), stage="index",
            )
            logger.info(f"SLOP GATE: rejected {item['url']} (score={slop_score})")
            return None
        item["slop_score"] = slop_score
        item["slop_verdict"] = "pass"
        item["slop_signals"] = json.dumps(slop_signals[:25])

        session = self.Session()
        try:
            existing = session.execute(
                text("SELECT id FROM knowledge_items WHERE url = :url"),
                {"url": item["url"]}
            ).fetchone()

            if existing:
                # FTS stays in sync via knowledge_items_au trigger
                session.execute(
                    text("""UPDATE knowledge_items
                        SET title=:title, content_text=:content_text,
                            source_name=:source_name, source_category=:source_category,
                            content_hash=:content_hash, author=:author,
                            published_date=:published_date, language=:language,
                            word_count=:word_count, quality_score=:quality_score,
                            overall_rank=:overall_rank, authority_score=:authority_score,
                            freshness_score=:freshness_score, engagement_score=:engagement_score,
                            media_json=:media_json, slop_score=:slop_score,
                            slop_verdict=:slop_verdict, slop_signals=:slop_signals,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=:id"""),
                    {
                        "id": existing[0],
                        "title": item["title"],
                        "content_text": item["content_text"],
                        "source_name": item.get("source_name", ""),
                        "source_category": item.get("source_category", ""),
                        "content_hash": item.get("content_hash", ""),
                        "author": item.get("author") or "",
                        "published_date": item.get("published_date") or "",
                        "language": item.get("language", "en"),
                        "word_count": item["word_count"],
                        "quality_score": item["quality_score"],
                        "overall_rank": item["overall_rank"],
                        "authority_score": item["authority_score"],
                        "freshness_score": item["freshness_score"],
                        "engagement_score": item["engagement_score"],
                        "media_json": item.get("media_json") or "",
                        "slop_score": item.get("slop_score", 0.0),
                        "slop_verdict": item.get("slop_verdict", "pass"),
                        "slop_signals": item.get("slop_signals", ""),
                    }
                )
                session.commit()
                return existing[0]

            session.execute(
                text("""INSERT INTO knowledge_items
                    (url, title, source_name, source_category, content_text, content_hash,
                     author, published_date, language, word_count, quality_score,
                     overall_rank, authority_score, freshness_score, engagement_score, media_json,
                     slop_score, slop_verdict, slop_signals)
                    VALUES (:url, :title, :source_name, :source_category, :content_text, :content_hash,
                            :author, :published_date, :language, :word_count, :quality_score,
                            :overall_rank, :authority_score, :freshness_score, :engagement_score, :media_json,
                            :slop_score, :slop_verdict, :slop_signals)"""),
                {
                    "url": item["url"], "title": item["title"],
                    "source_name": item.get("source_name", ""),
                    "source_category": item.get("source_category", ""),
                    "content_text": item["content_text"],
                    "content_hash": item.get("content_hash", ""),
                    "author": item.get("author") or "",
                    "published_date": item.get("published_date") or "",
                    "language": item.get("language", "en"),
                    "word_count": item["word_count"],
                    "quality_score": item["quality_score"],
                    "overall_rank": item["overall_rank"],
                    "authority_score": item["authority_score"],
                    "freshness_score": item["freshness_score"],
                    "engagement_score": item["engagement_score"],
                    "media_json": item.get("media_json") or "",
                    "slop_score": item.get("slop_score", 0.0),
                    "slop_verdict": item.get("slop_verdict", "pass"),
                    "slop_signals": item.get("slop_signals", ""),
                }
            )
            session.commit()

            row = session.execute(
                text("SELECT id FROM knowledge_items WHERE url = :url"),
                {"url": item["url"]}
            ).fetchone()
            return row[0] if row else None

        except Exception as e:
            session.rollback()
            logger.error(f"Upsert error: {e}")
            return None
        finally:
            session.close()

    def update_source_stats(self, source_name: str, quality: float, trusted: bool):
        session = self.Session()
        try:
            existing = session.execute(
                text("SELECT total_items, avg_quality FROM source_stats WHERE source_name = :name"),
                {"name": source_name}
            ).fetchone()

            if existing:
                new_count = existing[0] + 1
                new_avg = (existing[1] * existing[0] + quality) / new_count
                session.execute(
                    text("""UPDATE source_stats SET total_items=:cnt, avg_quality=:avg,
                        last_crawl=CURRENT_TIMESTAMP, is_trusted=:trusted WHERE source_name=:name"""),
                    {"cnt": new_count, "avg": round(new_avg, 4), "trusted": 1 if trusted else 0, "name": source_name}
                )
            else:
                session.execute(
                    text("INSERT INTO source_stats (source_name, total_items, avg_quality, last_crawl, is_trusted) VALUES (:name, 1, :avg, CURRENT_TIMESTAMP, :trusted)"),
                    {"name": source_name, "avg": quality, "trusted": 1 if trusted else 0}
                )
            session.commit()
        except Exception as e:
            session.rollback()
        finally:
            session.close()

    # ============================================================
    # SEARCH (FTS5 BM25, field-weighted)
    # ============================================================

    # FTS5 operators that must never leak from user input into MATCH
    _FTS_UNSAFE = re.compile(r'["*()^:{}]')

    @classmethod
    def _fts_safe(cls, word: str) -> str:
        return cls._FTS_UNSAFE.sub("", word or "").strip()

    def _fts_rows(self, session, fts_query: str, category: Optional[str], limit: int):
        q = """SELECT ki.id, ki.url, ki.title, ki.source_name, ki.source_category,
                      ki.content_text, ki.author, ki.published_date, ki.word_count,
                      ki.quality_score, ki.overall_rank, ki.authority_score,
                      ki.freshness_score, ki.media_json,
                      bm25(knowledge_fts, :w0, :w1, :w2, :w3) AS bm25_rank
               FROM knowledge_fts fts
               JOIN knowledge_items ki ON ki.id = fts.rowid
               WHERE knowledge_fts MATCH :query"""
        params: Dict = {
            "query": fts_query,
            "w0": BM25_WEIGHTS[0], "w1": BM25_WEIGHTS[1],
            "w2": BM25_WEIGHTS[2], "w3": BM25_WEIGHTS[3],
        }

        if category:
            q += " AND ki.source_category = :cat"
            params["cat"] = category

        q += " ORDER BY bm25_rank LIMIT :lim"
        params["lim"] = limit
        return session.execute(text(q), params).fetchall()

    def _expand_terms(self, words: List[str], per_term: int = 3, cap: int = 8) -> List[str]:
        """Query expansion from the PPMI thesaurus when recall is low."""
        expansions: List[str] = []
        try:
            for word in words:
                for related, _ppmi in self.get_associations(word, per_term):
                    if related not in words and related not in expansions:
                        expansions.append(related)
                    if len(expansions) >= cap:
                        return expansions
        except Exception:
            pass
        return expansions

    def keyword_search(self, query_text: str, limit: int = 20,
                       category: Optional[str] = None) -> List[Dict]:
        """FTS5 keyword search: phrase-first, BM25 field weights, PPMI expansion."""
        session = self.Session()
        try:
            words = [
                safe for safe in (
                    self._fts_safe(w)
                    for w in re.split(r'\s+', clean_for_search(query_text))
                )
                if len(safe) > 1
            ]
            if not words:
                return []

            # Phrase-first for multi-word queries, OR fallback, then thesaurus expansion.
            # Every term is quoted so hyphens/operators can never break MATCH.
            rows = []
            if len(words) > 1:
                rows = self._fts_rows(session, '"' + " ".join(words) + '"', category, limit)
            if not rows:
                rows = self._fts_rows(session, " OR ".join(f'"{w}"' for w in words), category, limit)

            expanded_ids: set = set()
            if len(rows) < 5:
                expanded = self._expand_terms(words)
                if expanded:
                    extra = self._fts_rows(
                        session,
                        " OR ".join(f'"{w}"' for w in expanded),
                        category,
                        limit,
                    )
                    seen = {r[0] for r in rows}
                    new_rows = [r for r in extra if r[0] not in seen]
                    expanded_ids = {r[0] for r in new_rows}
                    rows = list(rows) + new_rows

            if not rows:
                return []

            raw_scores = [-(r[14] or 0.0) for r in rows]
            max_score = max(raw_scores) or 1.0

            results = []
            for row, raw in zip(rows, raw_scores):
                quality = row[9] or 0.0
                authority = row[11] or 0.0
                freshness = row[12] or 0.0
                text_score = raw / max_score
                if row[0] in expanded_ids:
                    text_score *= 0.5  # thesaurus matches must not outrank direct ones

                final_score = (
                    0.50 * text_score +
                    0.20 * authority +
                    0.15 * quality +
                    0.15 * freshness
                )

                results.append({
                    "id": row[0], "url": row[1], "title": row[2],
                    "source_name": row[3], "source_category": row[4],
                    "content_snippet": clean_for_search(row[5] or "")[:300],
                    "author": row[6], "published_date": row[7],
                    "word_count": row[8], "quality_score": round(quality, 3),
                    "overall_rank": round(row[10] or 0.0, 3),
                    "authority_score": round(authority, 3),
                    "freshness_score": round(freshness, 3),
                    "media": _parse_media(row[13]),
                    "similarity_score": round(final_score, 4),
                    "search_type": "keyword",
                })

            results.sort(key=lambda item: item["similarity_score"], reverse=True)
            return results
        except Exception as e:
            logger.error(f"Keyword search error: {e}")
            return []
        finally:
            session.close()

    def refresh_fts(self):
        """Rebuild the FTS index"""
        self._rebuild_fts()

    # ============================================================
    # NEAR-DUPLICATE FINGERPRINTS (SimHash)
    # ============================================================

    def find_near_duplicate(self, simhash_value: int, threshold: int = 10) -> Optional[str]:
        """Return the URL of a near-duplicate already stored, if any."""
        from ..dedup import simhash_bands, hamming_distance

        session = self.Session()
        try:
            bands = simhash_bands(simhash_value)
            params = {f"b{i}": band for i, band in enumerate(bands)}
            where = " OR ".join(f"band{i}=:b{i}" for i in range(8))
            rows = session.execute(
                text(f"""SELECT url, simhash FROM content_simhash
                         WHERE {where}
                         LIMIT 500"""),
                params,
            ).fetchall()

            for url, stored in rows:
                try:
                    if hamming_distance(int(stored), simhash_value) <= threshold:
                        return url
                except (TypeError, ValueError):
                    continue
            return None
        except Exception as e:
            logger.debug(f"SimHash lookup failed: {e}")
            return None
        finally:
            session.close()

    def store_simhash(self, url: str, simhash_value: int, item_id: Optional[int] = None):
        from ..dedup import simhash_bands

        bands = simhash_bands(simhash_value)
        columns = ", ".join(f"band{i}" for i in range(8))
        placeholders = ", ".join(f":b{i}" for i in range(8))
        params = {f"b{i}": band for i, band in enumerate(bands)}
        params.update({"url": url, "simhash": str(simhash_value), "item_id": item_id})

        session = self.Session()
        try:
            session.execute(
                text(f"""INSERT OR REPLACE INTO content_simhash
                        (url, simhash, {columns}, item_id)
                        VALUES (:url, :simhash, {placeholders}, :item_id)"""),
                params,
            )
            session.commit()
        except Exception as e:
            session.rollback()
            logger.debug(f"SimHash store failed: {e}")
        finally:
            session.close()

    # ============================================================
    # LINK GRAPH / PAGERANK
    # ============================================================

    def store_links(self, src_url: str, links: List[str]):
        """Replace the outbound links for a crawled URL."""
        cleaned = []
        seen = set()
        for link in links or []:
            if not isinstance(link, str) or not link.startswith(("http://", "https://")):
                continue
            if link in seen:
                continue
            seen.add(link)
            cleaned.append(link)
            if len(cleaned) >= 100:
                break

        session = self.Session()
        try:
            session.execute(text("DELETE FROM page_links WHERE src_url = :src"), {"src": src_url})
            for dst in cleaned:
                session.execute(
                    text("INSERT INTO page_links (src_url, dst_url) VALUES (:src, :dst)"),
                    {"src": src_url, "dst": dst},
                )
            session.commit()
        except Exception as e:
            session.rollback()
            logger.debug(f"Link store failed for {src_url}: {e}")
        finally:
            session.close()

    def get_links(self, limit: int = 500_000) -> List[Tuple[str, str]]:
        session = self.Session()
        try:
            rows = session.execute(
                text("SELECT src_url, dst_url FROM page_links LIMIT :lim"),
                {"lim": limit},
            ).fetchall()
            return [(r[0], r[1]) for r in rows]
        except Exception as e:
            logger.debug(f"Link load failed: {e}")
            return []
        finally:
            session.close()

    def get_item_authorities(self) -> List[Tuple[int, str, float]]:
        session = self.Session()
        try:
            rows = session.execute(
                text("SELECT id, url, authority_score FROM knowledge_items")
            ).fetchall()
            return [(r[0], r[1], r[2] or 0.0) for r in rows]
        except Exception as e:
            logger.debug(f"Authority load failed: {e}")
            return []
        finally:
            session.close()

    def update_authority_scores(self, updates: List[Tuple[float, int]]) -> int:
        session = self.Session()
        try:
            for authority, item_id in updates:
                session.execute(
                    text("UPDATE knowledge_items SET authority_score = :auth WHERE id = :id"),
                    {"auth": round(float(authority), 4), "id": item_id},
                )
            session.commit()
            return len(updates)
        except Exception as e:
            session.rollback()
            logger.error(f"Authority update failed: {e}")
            return 0
        finally:
            session.close()

    # ============================================================
    # PPMI THESAURUS
    # ============================================================

    def replace_associations(self, rows: List[Tuple[str, str, float, int]]) -> int:
        """Replace the thesaurus with a freshly built set of associations."""
        session = self.Session()
        try:
            session.execute(text("DELETE FROM term_associations"))
            for term, related, ppmi, cooc in rows:
                session.execute(
                    text("""INSERT OR REPLACE INTO term_associations
                            (term, related, ppmi, cooc) VALUES (:term, :related, :ppmi, :cooc)"""),
                    {"term": term, "related": related, "ppmi": ppmi, "cooc": cooc},
                )
            session.commit()
            return len(rows)
        except Exception as e:
            session.rollback()
            logger.error(f"Thesaurus store failed: {e}")
            return 0
        finally:
            session.close()

    def get_associations(self, term: str, limit: int = 10) -> List[Tuple[str, float]]:
        session = self.Session()
        try:
            rows = session.execute(
                text("""SELECT related, ppmi FROM term_associations
                        WHERE term = :term ORDER BY ppmi DESC, cooc DESC LIMIT :lim"""),
                {"term": term.lower(), "lim": limit},
            ).fetchall()
            return [(r[0], r[1]) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    # ============================================================
    # QUERY STATS (aggregated, privacy-preserving)
    # ============================================================

    def log_query(self, query: str):
        """Increment the aggregate count for a normalized query. No identifiers stored."""
        normalized = " ".join((query or "").lower().split())[:200]
        if len(normalized) < 2:
            return

        session = self.Session()
        try:
            session.execute(
                text("""INSERT INTO query_stats (query, count, last_seen)
                        VALUES (:query, 1, CURRENT_TIMESTAMP)
                        ON CONFLICT(query) DO UPDATE SET
                            count = count + 1,
                            last_seen = CURRENT_TIMESTAMP"""),
                {"query": normalized},
            )
            session.commit()
        except Exception as e:
            session.rollback()
            logger.debug(f"Query log failed: {e}")
        finally:
            session.close()

    def get_popular_queries(self, limit: int = 10, prefix: str = "") -> List[Tuple[str, int]]:
        session = self.Session()
        try:
            if prefix:
                rows = session.execute(
                    text("""SELECT query, count FROM query_stats
                            WHERE query LIKE :prefix
                            ORDER BY count DESC, last_seen DESC LIMIT :lim"""),
                    {"prefix": f"{prefix.lower()}%", "lim": limit},
                ).fetchall()
            else:
                rows = session.execute(
                    text("SELECT query, count FROM query_stats ORDER BY count DESC, last_seen DESC LIMIT :lim"),
                    {"lim": limit},
                ).fetchall()
            return [(r[0], r[1]) for r in rows]
        except Exception:
            return []
        finally:
            session.close()

    def get_stats(self) -> Dict:
        if not self.Session:
            return {"total_items": 0, "categories": {}, "top_sources": []}

        session = self.Session()
        try:
            total = session.execute(text("SELECT COUNT(*) FROM knowledge_items")).scalar() or 0

            cats = session.execute(
                text("SELECT source_category, COUNT(*) FROM knowledge_items GROUP BY source_category ORDER BY COUNT(*) DESC")
            ).fetchall()

            srcs = session.execute(
                text("SELECT source_name, COUNT(*), AVG(quality_score) FROM knowledge_items GROUP BY source_name ORDER BY COUNT(*) DESC LIMIT 20")
            ).fetchall()

            return {
                "total_items": total,
                "categories": {r[0]: r[1] for r in cats},
                "top_sources": [{"name": r[0], "count": r[1], "avg_quality": round(r[2] or 0, 2)} for r in srcs],
            }
        except Exception as e:
            logger.error(f"Stats error: {e}")
            return {"total_items": 0, "categories": {}, "top_sources": []}
        finally:
            session.close()

    def close(self):
        if self.engine:
            self.engine.dispose()


def _parse_media(media_json: Optional[str]) -> List[Dict]:
    if not media_json:
        return []
    try:
        parsed = json.loads(media_json)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
