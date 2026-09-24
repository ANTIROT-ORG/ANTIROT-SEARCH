"""
Antirot Indexers — classical IR only (no neural nets).

Components:
- turso_indexer.py: Golden-layer indexer (FTS5 + BM25 + source stats)
- hf_uploader.py:   Raw-layer archive uploader (HuggingFace datasets)

Ranking is text-based (FTS5 BM25) blended with static authority,
freshness and source-trust signals. No embeddings, no neural models.
"""

__all__ = []
