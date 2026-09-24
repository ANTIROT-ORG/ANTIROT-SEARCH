# ratsearch-index

Tantivy serving index for Antirot (formerly Capybara / RatSearch) — classical BM25, no ML.

Syncs the SQLite golden layer (`knowledge_items`) into a Tantivy index and
serves it over HTTP. The Astro backend uses it automatically when
`TANTIVY_URL` is set, and falls back to SQLite FTS5 when it is not.

## Build

```bash
cd rust_search
cargo build --release
```

## Sync (rebuild from SQLite)

```bash
./target/release/ratsearch-index sync \
  --db ../search/data/local.db \
  --index ../search/data/tantivy
```

Atomic: builds into `tantivy.tmp` and swaps on success.

## Search (CLI)

```bash
./target/release/ratsearch-index search \
  --index ../search/data/tantivy \
  --query "climate change" --limit 10
```

## Serve

```bash
./target/release/ratsearch-index serve \
  --index ../search/data/tantivy --port 8090
```

Endpoints:

- `GET /health` → `ok`
- `GET /search?q=&limit=&category=` → JSON results with the same scoring
  blend as Python/TS: `0.50 text + 0.20 authority + 0.15 quality + 0.15 freshness`

## Wiring into the web app

```bash
# web/.env
TANTIVY_URL=http://127.0.0.1:8090
```

## Pipeline integration (optional)

```bash
RATSEARCH_TANTIVY_BIN=rust_search/target/release/ratsearch-index \
RATSEARCH_TANTIVY_INDEX=search/data/tantivy \
python -m search.pipeline
```

## Ranking

- phrase-first (title boosted 3×), OR fallback for recall
- OR-only matches get a 0.5× text penalty so direct matches always win
- no embeddings, no neural models

## Tests

```bash
cargo test
```
