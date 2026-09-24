"""
Link-graph PageRank (classical IR, no ML).

Power iteration over the crawled link graph. Scores are blended with the
existing static authority (domain trust) and written back to the index.
"""

import logging
from typing import Dict, Iterable, List, Tuple

logger = logging.getLogger("capybara.pagerank")

DAMPING = 0.85
ITERATIONS = 30
TOLERANCE = 1e-9


def pagerank(edges: Dict[str, List[str]], damping: float = DAMPING,
             iterations: int = ITERATIONS, tolerance: float = TOLERANCE) -> Dict[str, float]:
    """Compute PageRank over a directed graph {src: [dst, ...]}."""
    nodes = set(edges.keys())
    for destinations in edges.values():
        nodes.update(destinations)
    if not nodes:
        return {}

    n = len(nodes)
    out_links: Dict[str, List[str]] = {
        src: sorted({dst for dst in dsts if dst in nodes})
        for src, dsts in edges.items()
    }

    ranks = {node: 1.0 / n for node in nodes}
    dangling_nodes = [node for node in nodes if not out_links.get(node)]

    for _ in range(iterations):
        new = {node: (1.0 - damping) / n for node in nodes}

        dangling_sum = sum(ranks[node] for node in dangling_nodes)
        if dangling_sum:
            share = damping * dangling_sum / n
            for node in nodes:
                new[node] += share

        for src, destinations in out_links.items():
            if not destinations:
                continue
            share = damping * ranks[src] / len(destinations)
            for dst in destinations:
                new[dst] += share

        diff = sum(abs(new[node] - ranks[node]) for node in nodes)
        ranks = new
        if diff < tolerance:
            break

    return ranks


def normalize(ranks: Dict[str, float]) -> Dict[str, float]:
    if not ranks:
        return {}
    top = max(ranks.values())
    if top <= 0:
        return {node: 0.0 for node in ranks}
    return {node: value / top for node, value in ranks.items()}


def build_edges(links: Iterable[Tuple[str, str]]) -> Dict[str, List[str]]:
    edges: Dict[str, List[str]] = {}
    for src, dst in links:
        if not src or not dst or src == dst:
            continue
        edges.setdefault(src, []).append(dst)
    return edges


def apply_pagerank(layer, damping: float = DAMPING, iterations: int = ITERATIONS) -> Dict:
    """
    Compute PageRank from the layer's stored links and blend it into
    knowledge_items.authority_score: 0.5 * static authority + 0.5 * pagerank.
    """
    rows = layer.get_links()
    edges = build_edges(rows)
    ranks = normalize(pagerank(edges, damping=damping, iterations=iterations))

    updates: List[Tuple[float, int]] = []
    for item_id, url, static_authority in layer.get_item_authorities():
        graph_score = ranks.get(url)
        if graph_score is None:
            # Not part of the link graph yet — keep static authority unchanged
            continue
        blended = max(0.0, min(1.0, 0.5 * (static_authority or 0.0) + 0.5 * graph_score))
        updates.append((blended, item_id))

    updated = layer.update_authority_scores(updates)

    stats = {
        "nodes": len(ranks),
        "edges": sum(len(v) for v in edges.values()),
        "updated": updated,
    }
    logger.info(
        f"PageRank: {stats['nodes']} nodes, {stats['edges']} edges, "
        f"{updated} items updated"
    )
    return stats
