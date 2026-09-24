"""
PPMI co-occurrence thesaurus (classical distributional semantics, no ML).

Builds term associations from a sliding token window over the corpus and
scores them with Positive Pointwise Mutual Information:

    PPMI(a, b) = max(0, log2( P(a,b) / (P(a) * P(b)) ))

Used for related searches and low-recall query expansion.
"""

import math
import os
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

TOKEN_RE = re.compile(r"[a-z][a-z0-9-]{3,}")

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "this",
    "that", "these", "those", "they", "them", "their", "there", "which",
    "who", "whom", "what", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "not",
    "only", "own", "same", "than", "too", "very", "just", "because", "also",
    "into", "over", "after", "before", "between", "under", "about", "while",
    "using", "used", "use", "one", "two", "first", "also", "its", "his",
    "her", "our", "your", "you", "these", "many", "much", "often", "well",
    "made", "make", "like", "other", "new", "may", "see", "however", "known",
    "including", "include", "within", "without", "part", "form", "based",
}

WINDOW = 8
MIN_COOC = 3
MIN_DF = 2
TOP_N = 15
MAX_DOCS = 5000
MAX_TOKENS_PER_DOC = 2000


def tokenize(text: str) -> List[str]:
    return [
        token for token in TOKEN_RE.findall((text or "").lower())
        if token not in STOPWORDS
    ]


def build_associations(
    texts: Iterable[str],
    window: int = WINDOW,
    top_n: int = TOP_N,
    min_cooc: int = MIN_COOC,
    min_df: int = MIN_DF,
) -> List[Tuple[str, str, float, int]]:
    """Return (term, related, ppmi, cooc) rows, top_n per term."""
    df: Counter = Counter()
    cooc: Counter = Counter()

    for doc_index, text in enumerate(texts):
        if doc_index >= MAX_DOCS:
            break
        tokens = tokenize(text)[:MAX_TOKENS_PER_DOC]
        if len(tokens) < 5:
            continue

        df.update(set(tokens))

        for i, left in enumerate(tokens):
            for right in tokens[i + 1:i + 1 + window]:
                if left == right:
                    continue
                key = (left, right) if left < right else (right, left)
                cooc[key] += 1

    vocab = {term for term, count in df.items() if count >= min_df}
    pairs = {
        pair: count for pair, count in cooc.items()
        if count >= min_cooc and pair[0] in vocab and pair[1] in vocab
    }
    if not pairs:
        return []

    pair_total = sum(pairs.values())
    df_total = sum(df[term] for term in vocab) or 1

    by_term: Dict[str, List[Tuple[str, float, int]]] = defaultdict(list)
    for (left, right), count in pairs.items():
        p_pair = count / pair_total
        p_left = df[left] / df_total
        p_right = df[right] / df_total
        if p_pair <= 0 or p_left <= 0 or p_right <= 0:
            continue
        ppmi = max(0.0, math.log2(p_pair / (p_left * p_right)))
        if ppmi <= 0:
            continue
        by_term[left].append((right, ppmi, count))
        by_term[right].append((left, ppmi, count))

    rows: List[Tuple[str, str, float, int]] = []
    for term, related in by_term.items():
        related.sort(key=lambda item: (-item[1], -item[2]))
        for other, ppmi, count in related[:top_n]:
            rows.append((term, other, round(ppmi, 4), count))

    return rows
