"""
Content Processing — Structured data extraction, dedup, quality signals.

Layer 3 from the architecture diagram:
  - Content Extraction (parse HTML, remove boilerplate)
  - Text Cleaning & Normalization (dedup, normalize, detect language)
  - Content Understanding (tokenization, heading extraction, link analysis)
  - Structured Data Extraction (schema.org, Open Graph, meta tags)
"""

import re
import json
import hashlib
import logging
from typing import Dict, List, Optional, Tuple
from collections import Counter
from urllib.parse import urlparse

logger = logging.getLogger("capybara.content_processor")


# ============================================================
# SCHEMA.ORG / STRUCTURED DATA EXTRACTION
# ============================================================

def extract_structured_data(html: str) -> Dict:
    """
    Extract schema.org JSON-LD, Open Graph, and meta tag data.
    Returns a dict with structured metadata.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    result = {
        "schema_org": [],
        "open_graph": {},
        "twitter_card": {},
        "meta_tags": {},
        "microdata": [],
    }

    # ── JSON-LD (schema.org) ──
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                result["schema_org"].extend(data)
            elif isinstance(data, dict):
                result["schema_org"].append(data)
        except (json.JSONDecodeError, TypeError):
            pass

    # ── Open Graph ──
    for meta in soup.find_all("meta", property=True):
        prop = meta.get("property", "")
        if prop.startswith("og:"):
            key = prop[3:]
            result["open_graph"][key] = meta.get("content", "")

    # ── Twitter Card ──
    for meta in soup.find_all("meta", attrs={"name": True}):
        name = meta.get("name", "")
        if name.startswith("twitter:"):
            key = name[8:]
            result["twitter_card"][key] = meta.get("content", "")

    # ── Standard Meta Tags ──
    important_meta = [
        "description", "author", "keywords", "robots",
        "date", "pubdate", "publish_date", "last-modified",
        "article:published_time", "article:modified_time",
        "article:author", "article:section", "article:tag",
    ]
    for meta in soup.find_all("meta"):
        name = meta.get("name", "").lower()
        prop = meta.get("property", "").lower()
        key = name or prop
        if any(k in key for k in important_meta):
            result["meta_tags"][key] = meta.get("content", "")

    # ── Microdata (itemscope) ──
    for item in soup.find_all(attrs={"itemscope": True}):
        item_type = item.get("itemtype", "")
        if item_type:
            result["microdata"].append({"type": item_type})

    return result


def extract_author_from_structured(structured: Dict) -> str:
    """Extract best author from structured data"""
    # Try JSON-LD
    for schema in structured.get("schema_org", []):
        author = schema.get("author")
        if isinstance(author, str):
            return author
        if isinstance(author, dict):
            return author.get("name", "")
        if isinstance(author, list) and author:
            first = author[0]
            if isinstance(first, dict):
                return first.get("name", "")
            if isinstance(first, str):
                return first

    # Try Open Graph
    og_author = structured.get("open_graph", {}).get("article:author", "")
    if og_author:
        return og_author

    # Try meta tags
    for key in ["author", "article:author"]:
        if key in structured.get("meta_tags", {}):
            return structured["meta_tags"][key]

    return ""


def extract_date_from_structured(structured: Dict) -> str:
    """Extract best publication date from structured data"""
    # Try JSON-LD
    for schema in structured.get("schema_org", []):
        for date_key in ["datePublished", "dateCreated"]:
            if date_key in schema:
                return schema[date_key]

    # Try Open Graph
    og_date = structured.get("open_graph", {}).get("article:published_time", "")
    if og_date:
        return og_date

    # Try meta tags
    for key in ["date", "pubdate", "publish_date", "article:published_time"]:
        if key in structured.get("meta_tags", {}):
            return structured["meta_tags"][key]

    return ""


def extract_content_type_from_structured(structured: Dict) -> str:
    """Determine content type from structured data"""
    for schema in structured.get("schema_org", []):
        t = schema.get("@type", "").lower()
        if "article" in t:
            return "article"
        if "blog" in t:
            return "blog_post"
        if "recipe" in t:
            return "recipe"
        if "faq" in t or "questions" in t:
            return "faq"
        if "howto" in t or "how-to" in t:
            return "how_to"
        if "event" in t:
            return "event"
        if "product" in t:
            return "product"
        if "organization" in t:
            return "organization"
        if "person" in t:
            return "person"
        if "book" in t:
            return "book"
        if "course" in t:
            return "course"

    return "webpage"


# ============================================================
# CONTENT QUALITY SIGNALS
# ============================================================

def calculate_content_quality(page: Dict) -> Dict:
    """
    Calculate quality signals for content.
    Returns additional quality metrics beyond the basic scorer.
    """
    content = page.get("content_text", "")
    title = page.get("title", "")
    words = content.split()
    word_count = len(words)

    signals = {}

    # ── Factual density ──
    factual = 0
    for word in words:
        if re.match(r'\d+', word):
            factual += 1
        elif re.match(r'\[\d+\]', word):
            factual += 2
        elif re.match(r'\(\d{4}\)', word):
            factual += 2
    signals["factual_density"] = factual / max(word_count, 1)

    # ── Readability (Flesch-Kincaid approximation) ──
    sentences = re.split(r'[.!?]+', content)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
    if sentences:
        avg_sentence_len = sum(len(s.split()) for s in sentences) / len(sentences)
        avg_syllables = sum(_count_syllables(w) for w in words) / max(word_count, 1)
        # Simplified Flesch-Kincaid
        signals["readability_grade"] = round(
            0.39 * avg_sentence_len + 11.8 * avg_syllables - 15.59, 1
        )
    else:
        signals["readability_grade"] = 0

    # ── Content depth ──
    signals["word_count"] = word_count
    signals["sentence_count"] = len(sentences)
    signals["avg_sentence_length"] = (
        sum(len(s.split()) for s in sentences) / max(len(sentences), 1)
    )

    # ── Heading quality ──
    headings = page.get("heading_structure", {})
    h1_count = len(headings.get("h1", []))
    h2_count = len(headings.get("h2", []))
    h3_count = len(headings.get("h3", []))
    signals["heading_structure"] = {
        "h1_count": h1_count,
        "h2_count": h2_count,
        "h3_count": h3_count,
        "has_good_structure": h1_count == 1 and h2_count >= 2,
    }

    # ── Link quality ──
    internal = page.get("internal_links", [])
    external = page.get("external_links", [])
    signals["link_signals"] = {
        "internal_count": len(internal),
        "external_count": len(external),
        "has_citations": any(
            "wikipedia" in l or "scholar" in l or "doi.org" in l
            for l in external
        ),
    }

    # ── Media richness ──
    media = page.get("media", [])
    signals["media_signals"] = {
        "total_media": len(media),
        "images": len([m for m in media if m.get("type") == "image"]),
        "videos": len([m for m in media if m.get("type") in ("video", "video_embed")]),
        "documents": len([m for m in media if m.get("type") == "document"]),
    }

    # ── Title quality ──
    signals["title_quality"] = {
        "length": len(title),
        "word_count": len(title.split()),
        "has_colon": ":" in title,
        "has_question": "?" in title,
        "optimal_length": 30 <= len(title) <= 70,
    }

    # ── Language quality ──
    signals["language"] = {
        "diversity": _vocabulary_diversity(words),
        "avg_word_length": sum(len(w) for w in words) / max(word_count, 1),
        "complexity": _word_complexity(words),
    }

    return signals


def _count_syllables(word: str) -> int:
    """Rough syllable count"""
    word = word.lower().strip()
    if len(word) <= 2:
        return 1
    vowels = "aeiouy"
    count = 0
    prev_vowel = False
    for char in word:
        is_vowel = char in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def _vocabulary_diversity(words: List[str]) -> float:
    """Type-token ratio"""
    if not words:
        return 0.0
    unique = set(w.lower() for w in words if len(w) > 2)
    return len(unique) / max(len(words), 1)


def _word_complexity(words: List[str]) -> str:
    """Categorize vocabulary complexity"""
    if not words:
        return "unknown"
    avg_len = sum(len(w) for w in words) / len(words)
    if avg_len > 6:
        return "advanced"
    elif avg_len > 4.5:
        return "intermediate"
    else:
        return "basic"


# ============================================================
# CONTENT DEDUPLICATION
# ============================================================

class ContentDeduplicator:
    """
    Multi-level content deduplication:
    1. Exact hash match (SHA-256)
    2. Near-duplicate detection (similarity threshold)
    3. URL normalization
    """

    def __init__(self):
        self.seen_hashes: set = set()
        self._max_hashes = 1_000_000

    def is_duplicate(self, content_text: str, url: str) -> bool:
        """Check if content is a duplicate"""
        content_hash = self._content_hash(content_text)

        if content_hash in self.seen_hashes:
            return True

        self.seen_hashes.add(content_hash)
        if len(self.seen_hashes) > self._max_hashes:
            # Evict oldest 10%
            for _ in range(self._max_hashes // 10):
                self.seen_hashes.pop()

        return False

    def _content_hash(self, text: str) -> str:
        """Generate content hash (normalized)"""
        normalized = re.sub(r'\s+', ' ', text.lower().strip())
        return hashlib.sha256(normalized.encode()).hexdigest()

    def near_duplicate(self, text_a: str, text_b: str, threshold: float = 0.85) -> bool:
        """Check if two texts are near-duplicates using Jaccard similarity"""
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())

        if not words_a or not words_b:
            return False

        intersection = len(words_a & words_b)
        union = len(words_a | words_b)

        return (intersection / union) >= threshold if union > 0 else False


# ============================================================
# LANGUAGE DETECTION (simple heuristic)
# ============================================================

COMMON_WORDS = {
    "en": {"the", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
           "do", "does", "did", "will", "would", "could", "should", "may", "might", "can",
           "this", "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
           "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from"},
    "es": {"el", "la", "los", "las", "un", "una", "es", "son", "está", "están", "ser",
           "haber", "hacer", "ir", "poder", "decir", "en", "de", "que", "por", "con",
           "para", "como", "pero", "más", "este", "esta", "ese", "esa", "yo", "tú", "él"},
    "fr": {"le", "la", "les", "un", "une", "est", "sont", "être", "avoir", "faire",
           "dire", "aller", "pouvoir", "vouloir", "en", "de", "que", "pour", "avec",
           "dans", "sur", "mais", "plus", "ce", "cette", "je", "tu", "il", "elle"},
    "de": {"der", "die", "das", "ein", "eine", "ist", "sind", "sein", "haben", "werden",
           "können", "müssen", "sollen", "wollen", "in", "von", "zu", "mit", "auf",
           "für", "aber", "auch", "nicht", "noch", "ich", "du", "er", "sie", "es"},
    "pt": {"o", "a", "os", "as", "um", "uma", "é", "são", "estar", "ser", "ter",
           "fazer", "ir", "poder", "dizer", "em", "de", "que", "para", "com",
           "por", "mais", "mas", "este", "esta", "eu", "tu", "ele", "ela"},
}


def detect_language(text: str) -> str:
    """Simple heuristic language detection"""
    words = set(re.findall(r'\w+', text.lower()))
    scores = {}
    for lang, common in COMMON_WORDS.items():
        scores[lang] = len(words & common)

    if scores:
        best = max(scores, key=scores.get)
        if scores[best] > 5:
            return best
    return "en"


# ============================================================
# MAIN PROCESSOR
# ============================================================

class ContentProcessor:
    """Full content processing pipeline"""

    def __init__(self):
        self.dedup = ContentDeduplicator()

    def process_page(self, html: str, url: str, source_name: str = "") -> Optional[Dict]:
        """
        Full processing pipeline for a crawled page.
        Returns enriched page data or None if filtered out.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # ── Extract structured data ──
        structured = extract_structured_data(html)

        # ── Extract main content ──
        for tag in soup.find_all(["script", "style", "noscript", "iframe",
                                   "nav", "footer", "aside"]):
            tag.decompose()

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # Prefer structured data for author/date
        author = extract_author_from_structured(structured)
        published_date = extract_date_from_structured(structured)
        content_type = extract_content_type_from_structured(structured)

        # Fallback meta extraction
        if not author:
            meta_author = soup.find("meta", attrs={"name": "author"})
            author = meta_author.get("content", "") if meta_author else ""

        if not published_date:
            for attr_name in ["article:published_time", "date", "pubdate"]:
                meta_date = soup.find("meta", attrs={"property": attr_name}) or \
                           soup.find("meta", attrs={"name": attr_name})
                if meta_date:
                    published_date = meta_date.get("content", "")
                    break

        # ── Main content ──
        main = (soup.find("article") or soup.find("main") or
                soup.find("div", {"role": "main"}) or soup.body)
        if main is None:
            return None

        content_text = re.sub(r'\s+', ' ', main.get_text(separator=" ", strip=True))
        if len(content_text) < 80:
            return None

        # ── Dedup check ──
        if self.dedup.is_duplicate(content_text, url):
            return None

        # ── Language detection ──
        language = detect_language(content_text[:2000])

        # ── Build page data ──
        page = {
            "url": url,
            "title": title,
            "content_text": content_text,
            "author": author,
            "published_date": published_date,
            "language": language,
            "word_count": len(content_text.split()),
            "content_type": content_type,
            "source_name": source_name,
            "schema_org_types": [s.get("@type", "") for s in structured.get("schema_org", [])],
            "has_structured_data": bool(structured.get("schema_org")),
            "open_graph": structured.get("open_graph", {}),
        }

        # ── Quality signals ──
        quality = calculate_content_quality(page)
        page["quality_signals"] = quality

        return page
