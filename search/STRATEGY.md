# Antirot — Search Engine Strategy

**Product name:** Antirot (formerly Capybara, earlier RatSearch) — the repo,
crawler package and some internal namespaces (`RATSEARCH_*` env vars,
`ratsearch:` cache keys, `ratsearch-index` binary, the `RatSearch` robots
UA-token) keep their legacy identifiers deliberately.

**Decision (locked):** strictly no neural nets. No LLM-generated content indexed,
no embeddings, no trained classifiers in ranking. Classical IR and statistics only:
BM25/FTS5, PageRank/TrustRank, SimHash, TF-IDF/LSI (SVD), PPMI thesaurus,
n-gram language models for slop detection.

**Goal:** Google-like search experience over curated human knowledge — fast,
explainable ("Thinking"), and free of AI slop.

---

## 1. Where we started (Sep 2026 audit)

| Area | State before |
|---|---|
| Corpus | 176 items indexed (142 Wikipedia) — effectively empty |
| Semantic search | Dead code: API always passed `queryEmbedding = null` |
| Crawl coverage | Stalled: persistent content-hash set skipped re-indexing; 30 URLs/source cap |
| Freshness | `get_due_sources()` computed but never used; recorded fake source names |
| Ranking | FTS5 `OR` query; BM25 recomputed in JS over a pre-filtered subset |
| Query/ranking logic | Duplicated in Python and TypeScript, divergent |
| Images tab | Broken: `media` discovered at crawl time but never stored in DB |
| Slop detector | 10 regex/statistical layers, hard thresholds, no provenance gate |
| Neural dependency | `sentence-transformers` (removed) |

## 2. Architecture

```
Curated sources + GitHub API + sitemaps
        │
        ▼
[Frontier/Scheduler]  recrawl by change frequency (freshness.py)
        │
        ▼
[Fetcher]  robots.txt (UA groups, wildcards), sitemaps, conditional GET, per-host delay
        │
        ▼
[Extractor]  readability text, headings, links, media, structured data, canonical
        │
        ▼
[Dedup]  sha256 exact + in-run content hash dedup (+ SimHash/LSH later)
        │
        ▼
[Human-content gate]  provenance + heuristics + statistics (+ n-gram LM later)
        │
        ▼
[Indexer]  FTS5 (field-weighted BM25) + static signals, sync triggers
        │
        ▼
[Serving]  0.50 text + 0.20 authority + 0.15 quality + 0.15 freshness
        │
        ▼
[Astro SSR UI]  /search, /code, /images, Thinking panel (phase 1+)
```

## 3. Ranking model (no AI)

- **Text:** FTS5 BM25 field weights `title=3.0, source=1.5, body=1.0, author=0.5`;
  phrase-first queries with OR fallback.
- **Static:** curated source quality + domain authority + (later) PageRank/TrustRank.
- **Freshness:** exponential decay per published date.
- **Later:** MMR diversity, per-domain caps, click-log priors (aggregate only).

## 4. Slop detection v3 (planned, phase 1)

1. Provenance gate: allowlist, author, date, citations, schema.org, site quality.
2. Statistical: n-gram LM perplexity + burstiness, zlib ratio, TTR, clause variance.
3. Template/repetition: shingle self-similarity, per-host boilerplate ratio.
4. Substance: citation/entity/rare-term density.
5. Calibrated ensemble per trust tier, review band, archive all signals, appeal path.

`search/filters/` already implements the heuristic/core layers; phase 1 adds the
statistical LM and provenance gate.

## 5. Verticals & federation (Astro API backend)

The Astro Node server (`web/`) is the single backend. `/api/search?vertical=…`
federates the local FTS5 index with keyless and keyed providers, applies the
slop gate, dedupes, scores and returns one normalized result shape.

| Tab | Providers |
|---|---|
| All | local index · Wikidata knowledge card · OpenAlex · Crossref · GDELT |
| Photos | local media · Wikimedia Commons · Openverse · Internet Archive |
| Videos | local video embeds · YouTube (key) · Internet Archive |
| News | curated RSS feeds (16 reputable outlets) · GDELT (allowlisted domains) |
| Code | GitHub Code Search (token) · local docs index |

- **Clean-news policy:** RSS feed list + GDELT domain allowlist (wires, public
  broadcasters, quality press, journals, `.gov/.edu/.int`). Social platforms and
  content farms are denied in `web/src/lib/providers/slop.ts` and can never surface.
- **Keys** live in `web/.env` (see `web/.env.example`): `GITHUB_TOKEN` (Code),
  `YOUTUBE_API_KEY` (Videos), optional `OPENALEX_MAILTO`, `REDIS_URL`.
- **Caching:** Redis when configured, in-process TTL map otherwise; failed provider
  lookups cached briefly, GDELT has a 15-min circuit breaker after two failures.
- **Scoring:** provider weight × lexical relevance × freshness (news/videos) ×
  slop penalty. Local BM25 score is used directly.
- **Rust path:** when QPS outgrows Node, only the serving index moves (Tantivy);
  provider/federation contracts stay the same. Operator queries (`site:`,
  `filetype:`, `after:`/`before:`, `"phrase"`, `-exclusion`) and the `when`
  recency filter bypass Tantivy and run on FTS5 only (`web/src/lib/queryops.ts`
  + `local.ts`), since the Rust index has no filter fields.
- **SERP features:** knowledge panel (Wikidata claims/image/sitelinks with a
  7-day `knowledge_entities` cache), featured answer, people-also-ask, result
  sitelinks, did-you-mean (`web/src/lib/spell.ts`), operator chips + time
  filter in the UI. Domain diversity: max 2 results/host in the top 10.

## 6. Roadmap

- **Phase 0 — foundation (done):** freshness-driven crawl scheduling, per-URL change
  state + conditional GET, sitemap/robots correctness, configurable budgets, all-batch
  indexing, media persistence, FTS5 BM25 with sync triggers, `/api/suggest`,
  neural path removed, tests.
- **Phase 1 — verticals (done):** federated All/Photos/Videos/News/Code tabs on the
  Astro API backend, Wikidata knowledge card, clean-news allowlist, backend slop gate,
  provider caching + circuit breaker.
- **Phase 2 — quality & scale (done):** SimHash near-duplicate gate at index
  time (8×8-bit banding, content fingerprints), slop detector v3 (provenance gate +
  zlib compression ratio + burstiness), link-graph PageRank blended into authority,
  PPMI co-occurrence thesaurus (related searches + penalized low-recall query
  expansion), privacy-preserving query stats (aggregated, no identifiers),
  NDCG/MRR eval harness (`python -m search.eval`, baseline 1.0/1.0 on 15 queries).
  Validated with a live incremental crawl: 10 pages → 2 changed pages on re-run,
  links/media captured, PageRank graph built.
- **Common Crawl ingestion (done):** `python -m search.cc_ingest --domain X
  --per-domain N` discovers URLs via the CDX index (status 200, text/html,
  collapsed) and range-fetches WARC records from `data.commoncrawl.org` —
  no origin traffic. Respects `X-Robots-Tag: noindex`, skips records > 2 MB,
  retries CDX 504s with narrower path fallbacks (`/wiki/*`, `/docs/*`, `/en/*`).
  Output is standard `crawled_cc_*.jsonl`, so the normal filter → index pipeline
  (and slop/dedup gates) applies unchanged. Weekly in
  `.github/workflows/cc-ingest.yml` with `CRAWL_SKIP_CRAWL=true`.
  Limitation: CDX timeouts on very large domains (Wikipedia) — use dumps/API.
- **Phase 3 — harden (done):** Tantivy serving index in `rust_search/` (BM25
  phrase-first + shared ranking blend, HTTP API, Astro auto-detects `TANTIVY_URL`
  with FTS5 fallback, pipeline sync step, CI builds + uploads the index artifact),
  backups (`search/backup.py`: gzipped JSONL export + SQLite `VACUUM INTO`
  snapshot, HuggingFace upload with retention, daily workflow, restore command),
  CDN-ready caching (`s-maxage` + `stale-while-revalidate` on public endpoints,
  `no-store` on errors), public API docs (`/api-docs`, `/api/openapi.json`).
  Deferred until traffic requires it: index sharding and real CDN deployment.
- **Phase 4 — Google-grade SERP + defence-in-depth slop gate (done):**
  - **Slop gate — never indexed, no matter what:** four layers. (1) pipeline
    step 3 file split now carries `_slop_verdict` on accepted items too;
    (2) `upsert_knowledge_item` hard-fails on slop (re-running the detector
    inline when no verdict is carried) — covers pipeline, wiki crawlers, CC/Wiki
    dumps and backup restore — and persists `slop_score/slop_verdict/
    slop_signals` columns; (3) every rejection lands in the `slop_rejects`
    audit table, and `python -m search.purge_slop [--dry-run]` re-screens and
    purges anything indexed before the gate; (4) query-time `slop.ts` hard-drops
    AI-disclosure and clickbait results (penalty → drop).
  - **Query operators:** `"phrase"`, `-exclude`, `site:`, `filetype:`,
    `after:`/`before:` parsed before normalization (`queryops.ts`), applied as
    FTS clauses + row filters; operator-only queries browse the index; `when=`
    recency filter; removable chips + deep links (`?q=&vertical=&when=`).
  - **Knowledge panel:** enriched Wikidata claims (curated ~35-property
    whitelist, batched label resolution, P18 image, sitelinks) cached locally
    in `knowledge_entities` (7-day TTL).
  - **SERP:** featured answer, people-also-ask accordion, per-result sitelinks
    (same-domain results + `page_links`/index fallback), did-you-mean via
    curated map + Levenshtein, domain diversity in the top 10.
  - **Dictionary instant answer (DuckDuckGo-style):** single-word lookups get
    a dictionary card — Wiktionary definitions (REST) + IPA/audio (parse API),
    pronunciation audio streamed through `/api/dict/audio` (https allowlist,
   1-year cache). Cached in `dictionary_cache` (30d hits /7d misses), raced
    with its own `dict_ms` budget; replaces the featured answer for lookups.
  - Eval unchanged after the upgrade: NDCG@10 1.0 / MRR 1.0 (15 queries).
  - **Freshness made real:** published dates are extracted as
    JSON-LD → meta → `<time>` → sitemap lastmod (captured *before* script
    stripping; RFC 2822 + ISO normalized via `normalize_date`), wiki-dump
    ingest now uses revision timestamps, and `python -m search.backfill_dates`
    fills existing rows from JSONL/sitemap/HTTP Last-Modified (never
    overwriting), nudging `overall_rank` by the freshness delta. Powers the
    `when=` filter and the0.15 freshness rank input; eval stayed1.0/1.0.
  - **Tests in CI:** `.github/workflows/test.yml` runs `pytest tests/test_capybara.py`
    plus `astro check` and `vitest` (40 tests covering query operators, spelling,
    PAA, answer and dictionary selection) on every push/PR.
  - **Breadth — a real web search engine:**
    - **Live providers (Brave-style meta search, no page storage):** Hacker News
      (Algolia), Stack Exchange/StackOverflow, Wikipedia search API, and Common
      Crawl CDX for `site:` operators (queryable index, lookup-oriented). All
      free APIs, merged in the Astro server (Turso = D1, existing cache/SWR = KV).
    - **Scheduled scraper across trusted human sources:** tech news (404 Media,
      Ars Technica, The Verge, Wired, TechCrunch, Lobsters, MIT Tech Review),
      journals (Nature, Quanta), health (WHO, NHS, Cochrane), Reddit subreddits
      (trends — Reddit hard-blocks bots, so best-effort) and 14 human YouTube
      channels via resolved RSS feeds. Feed items become normal crawl records
      (real dates!) and pass the same slop gate. New categories: `tech_news`,
      `health_medical`.
    - **GitHub repos:** `search/github_ingest.py` (weekly workflow) fetches top
      repos by topic → `crawled_gh_*.jsonl` → pipeline → index + raw HF archive.
    - **Progressive loading:** `fast=1` returns top-5 + knowledge immediately
      (knowledge panel is 7-day cached), the full federated response fills in
      below (`fast` and `full` are separately cached; SWR merges warm providers
      at ~45s). Browser demo: 3ms first paint → 300ms full.
  - **Latency budget (search in <50ms warm, ≤1.8s cold):**
    in-process response cache with stale-while-revalidate (`redis.ts` MEM
    fallback + `x-rat-refresh` background refresh at45s), per-provider budgets
    (300ms when local results exist,2s for news), knowledge-panel race capped
    at1.8s from t0, sitelink lookups limited to the top5 domains, SQLite
    `mmap/cache_size` PRAGMAs. Measured: repeat queries23-35ms, cold
   0.7-1.8s (was1.6-5.7s), suggest2ms.
- **Wikipedia dump ingestion (done):** `python -m search.wiki_dump --limit N`
  range-fetches the multistream index (partial bz2 download) and individual
  article streams from the 26 GB dump — no full download, no API limits.
  `--random --seed S` samples uniformly random streams across the whole file
  (the index only covers the pageid-ordered prefix, so random streams are how
  the corpus stays diverse). Wikitext is cleaned with a classical regex
  pipeline, internal `[[links]]` feed the PageRank graph, and output is
  standard `crawled_wiki_*.jsonl`. Weekly in
  `.github/workflows/wiki-ingest.yml`. Corpus after first imports: 834 items,
  PageRank 29k nodes / 34k edges, thesaurus 9.4k terms / 58k associations.
- **Slop-detector calibration (done):** the first Wikipedia import exposed heavy
  false positives (bare `meta`/`claude`/`gemini` regex matches). Strong signals
  (AI self-disclosure) are now separated from weak stylistic phrases, weak
  signals require high density, bare model names are no longer signals, and
  curated sources get a calibration dampener. Wikipedia pass rate went 39% → 100%
  while the AI-slop tests still pass.

## 7. Key files

- `search/pipeline.py` — crawl → process → filter → index
- `search/crawlers/__init__.py` — robots, sitemaps, conditional GET, budgets
- `search/freshness.py` — recrawl scheduler, change detection
- `search/filters/__init__.py` — slop detector v3 (index-time gate)
- `search/purge_slop.py` — re-screen + purge slop already in the index
- `search/dedup.py` — SimHash near-duplicate fingerprints
- `search/indexers/turso_indexer.py` — golden layer (FTS5, simhash, link graph)
- `search/indexers/pagerank.py` — link-graph authority
- `search/eval.py` — NDCG/MRR ranking evaluation
- `search/cc_ingest.py` — Common Crawl CDX discovery + WARC range fetch
- `search/wiki_dump.py` — Wikipedia multistream dump range import
- `search/backup.py` — JSONL export, SQLite snapshot, HF upload + restore
- `rust_search/` — Tantivy serving index (sync / search / serve), read its README
- `web/src/lib/turso.ts` — local serving search (query processing + BM25 blend)
- `web/src/lib/queryops.ts` — query operators + row filters
- `web/src/lib/spell.ts` — did-you-mean (ported from `search/query_processor.py`)
- `web/src/lib/providers/wikidata.ts` — knowledge panel data
- `web/src/lib/providers/paa.ts` — people-also-ask
- `web/src/lib/providers/` — federation: types, cache, slop, http, per-source
  providers (local, wikidata, commons, openverse, archive, youtube, rss, gdelt,
  papers, github), `federation.ts`, `related.ts`
- `web/src/pages/api/search.ts` — federated search API (`interpretation` = Thinking)
- `web/src/pages/api/suggest.ts` — autocomplete
- `web/.env.example` — provider keys

## 8. Metrics

p50 < 150 ms (cached), p95 < 400 ms warm search; zero-result rate < 5%; slop pass
rate tracked per source; NDCG@10 ≥ 0.6 on the eval set; crawl success > 95%.
