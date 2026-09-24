"""
SimHash near-duplicate detection (classical IR, no ML).

64-bit SimHash over 3-word shingles + Hamming distance.
Banding (8 × 8-bit) keeps candidate lookup cheap: any two fingerprints
within a Hamming distance of 7 share at least one band.
"""

import hashlib
import re
from collections import Counter
from typing import Iterable, List

_TOKEN_RE = re.compile(r"[a-z0-9']+")
BANDS = 8
BAND_BITS = 8
MASK = (1 << 64) - 1
DEFAULT_THRESHOLD = 10  # small edits land ~8-12; unrelated pairs ~28-34


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def simhash64(text: str) -> int:
    """
    64-bit SimHash over 3-word shingles (more stable than unigrams for
    short documents), falling back to tokens for very short text.
    Token hashes use blake2b for speed and stability.
    """
    tokens = tokenize(text)
    if not tokens:
        return 0

    if len(tokens) >= 3:
        features = [
            " ".join(tokens[i:i + 3])
            for i in range(len(tokens) - 2)
        ]
    else:
        features = tokens

    vector = [0] * 64
    for feature, weight in Counter(features).items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        for bit in range(64):
            if (h >> bit) & 1:
                vector[bit] += weight
            else:
                vector[bit] -= weight

    result = 0
    for bit in range(64):
        if vector[bit] > 0:
            result |= (1 << bit)
    return result


def simhash_bands(value: int) -> List[int]:
    return [(value >> (BAND_BITS * i)) & 0xFF for i in range(BANDS)]


def hamming_distance(a: int, b: int) -> int:
    return bin((a ^ b) & MASK).count("1")


def is_near_duplicate(a: int, b: int, threshold: int = DEFAULT_THRESHOLD) -> bool:
    return hamming_distance(a, b) <= threshold


def dedupe_texts(texts: Iterable[str], threshold: int = DEFAULT_THRESHOLD) -> List[int]:
    """Return indices of texts to keep (near-duplicates collapsed to first seen)."""
    kept: List[int] = []
    hashes: List[int] = []
    for index, text in enumerate(texts):
        h = simhash64(text)
        if any(is_near_duplicate(h, existing, threshold) for existing in hashes):
            continue
        hashes.append(h)
        kept.append(index)
    return kept
