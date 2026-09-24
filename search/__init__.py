"""
Antirot — AI-slop-free search engine for real human knowledge (formerly Capybara / RatSearch).

Classical IR only: no neural nets, no generative AI.

Components:
- sources.py: Curated list of human knowledge sources (sources.json)
- crawlers/: robots/sitemap crawler with conditional GET + change detection
- filters/: algorithmic AI-slop detection and quality filtering
- indexers/: FTS5/BM25 golden layer + HuggingFace raw archive
- query_processor.py: normalization, spelling, expansion, category inference
- pipeline.py: crawl → process → filter → index
- web/: Astro search interface (../web)
"""

__version__ = "2.0.0"
