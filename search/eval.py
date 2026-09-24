"""
Ranking evaluation harness — NDCG@k and MRR.

Usage:
    python -m search.eval            # evaluate against the golden layer
    python -m search.eval --k 5

The eval set uses URL substrings so it survives canonical-URL changes.
Extend EVAL_SET as the corpus grows; keep it fixed for comparable results.
"""

import argparse
import json
import math
import os
from typing import Dict, List, Optional

EVAL_SET: List[Dict] = [
    {"query": "quantum mechanics", "expected": ["en.wikipedia.org/wiki/Quantum_mechanics"]},
    {"query": "theory of relativity", "expected": ["en.wikipedia.org/wiki/Theory_of_relativity"]},
    {"query": "climate change", "expected": ["en.wikipedia.org/wiki/Climate_change", "en.wikipedia.org/wiki/Climate"]},
    {"query": "machine learning", "expected": ["en.wikipedia.org/wiki/Machine_learning"]},
    {"query": "black hole", "expected": ["en.wikipedia.org/wiki/Black_hole"]},
    {"query": "periodic table", "expected": ["en.wikipedia.org/wiki/Periodic_table"]},
    {"query": "renewable energy", "expected": ["en.wikipedia.org/wiki/Renewable_energy"]},
    {"query": "solar system", "expected": ["en.wikipedia.org/wiki/Solar_System"]},
    {"query": "ancient rome", "expected": ["en.wikipedia.org/wiki/Ancient_Rome"]},
    {"query": "game theory", "expected": ["en.wikipedia.org/wiki/Game_theory"]},
    {"query": "peer review", "expected": ["en.wikipedia.org/wiki/Peer_review"]},
    {"query": "great barrier reef", "expected": ["en.wikipedia.org/wiki/Great_Barrier_Reef"]},
    {"query": "organic chemistry", "expected": ["en.wikipedia.org/wiki/Organic_chemistry"]},
    {"query": "greek mythology", "expected": ["en.wikipedia.org/wiki/Greek_mythology"]},
    {"query": "plate tectonics", "expected": ["en.wikipedia.org/wiki/Plate_tectonics"]},
]


def dcg_at_k(relevances: List[int], k: int) -> float:
    total = 0.0
    for i, rel in enumerate(relevances[:k]):
        total += rel / math.log2(i + 2)
    return total


def ndcg_at_k(ranked_urls: List[str], expected: List[str], k: int = 10) -> float:
    relevances = [1 if any(exp in url for exp in expected) else 0 for url in ranked_urls[:k]]
    ideal = sorted(relevances, reverse=True)
    idcg = dcg_at_k(ideal, k)
    if idcg == 0:
        return 0.0
    return dcg_at_k(relevances, k) / idcg


def reciprocal_rank(ranked_urls: List[str], expected: List[str]) -> float:
    for i, url in enumerate(ranked_urls):
        if any(exp in url for exp in expected):
            return 1.0 / (i + 1)
    return 0.0


def evaluate(layer=None, k: int = 10, cases: Optional[List[Dict]] = None) -> Dict:
    from .indexers.turso_indexer import TursoGoldenLayer

    owns_layer = layer is None
    layer = layer or TursoGoldenLayer()

    cases = cases or EVAL_SET
    per_query = []

    for case in cases:
        results = layer.keyword_search(case["query"], limit=k)
        urls = [r["url"] for r in results]
        ndcg = ndcg_at_k(urls, case["expected"], k)
        rr = reciprocal_rank(urls, case["expected"])
        per_query.append({
            "query": case["query"],
            "ndcg": round(ndcg, 4),
            "rr": round(rr, 4),
            "hits": sum(1 for u in urls if any(e in u for e in case["expected"])),
            "returned": len(urls),
        })

    avg_ndcg = sum(q["ndcg"] for q in per_query) / max(len(per_query), 1)
    avg_mrr = sum(q["rr"] for q in per_query) / max(len(per_query), 1)

    summary = {
        "queries": len(per_query),
        "k": k,
        "ndcg_at_k": round(avg_ndcg, 4),
        "mrr": round(avg_mrr, 4),
        "per_query": per_query,
    }

    if owns_layer:
        layer.close()

    return summary


def main():
    parser = argparse.ArgumentParser(description="Antirot ranking evaluation")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    args = parser.parse_args()

    summary = evaluate(k=args.k)

    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print(f"Ranking evaluation (k={summary['k']}, {summary['queries']} queries)")
    print(f"NDCG@{summary['k']}: {summary['ndcg_at_k']}")
    print(f"MRR:       {summary['mrr']}")
    print()
    for row in summary["per_query"]:
        print(f"  {row['query']:<22} ndcg={row['ndcg']:<7} rr={row['rr']:<7} "
              f"hits={row['hits']}/{row['returned']}")


if __name__ == "__main__":
    main()
