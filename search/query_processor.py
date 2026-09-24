"""
Query Processing Pipeline
Handles query understanding, normalization, expansion, and spelling correction.

Pipeline:
  1. Normalization (lowercasing, whitespace, special chars)
  2. Spelling correction (edit-distance based)
  3. Synonym expansion (wordnet-backed)
  4. Stop word removal (smart filtering)
  5. Query decomposition (multi-word queries → intent detection)
  6. Category inference (auto-detect topic)
"""

import re
import math
import logging
from typing import List, Dict, Optional, Tuple
from collections import Counter

logger = logging.getLogger("capybara.query_processor")


# ============================================================
# COMMON MISSPELLINGS (curated for human knowledge queries)
# ============================================================

SPELLING_CORRECTIONS = {
    "reciepe": "recipe", "recipie": "recipe", "recepie": "recipe",
    "algorith": "algorithm", "algorithim": "algorithm", "algoritm": "algorithm",
    "pythn": "python", "pyhton": "python", "pyton": "python",
    "javscript": "javascript", "javasript": "javascript", "javascript": "javascript",
    "photoShop": "photoshop", "photoshopt": "photoshop",
    "mathmatics": "mathematics", "mathematics": "mathematics", "matheatics": "mathematics",
    "phisics": "physics", "phsyics": "physics", "phyics": "physics",
    "biolog": "biology", "bilogy": "biology",
    "chmistry": "chemistry", "chemestry": "chemistry",
    "progrmming": "programming", "programing": "programming", "programing": "programming",
    "nutriton": "nutrition", "nutrtion": "nutrition",
    "histroy": "history", "hsitory": "history",
    "geoagrophy": "geography", "gography": "geography",
    "cokking": "cooking", "coooking": "cooking",
    "cramming": "learning", "grammer": "grammar",
    "enviroment": "environment", "enviorment": "environment",
    "sustanable": "sustainable", "sustainble": "sustainable",
    "democrcy": "democracy", "demoracy": "democracy",
    "ecosytem": "ecosystem", "ecosystm": "ecosystem",
    "renassiance": "renaissance", "renaisance": "renaissance",
    "diabeties": "diabetes", "diabetus": "diabetes",
    "antibotic": "antibiotic", "antibitocs": "antibiotics",
    "astronomoy": "astronomy", "astronomy": "astronomy",
    "philosphy": "philosophy", "philospher": "philosopher",
    "psycology": "psychology", "psycholog": "psychology",
    "archeology": "archaeology", "archaeolgy": "archaeology",
    "linguistcs": "linguistics", "linguistis": "linguistics",
    "ecnomics": "economics", "economic": "economics",
    "thermodynaics": "thermodynamics", "thermodyamics": "thermodynamics",
    "evoluton": "evolution", "evolutiion": "evolution",
    "newton law": "newtons laws", "newton first law": "newtons first law",
    "spaceX": "spacex", "tesla motors": "tesla",
    "clmate change": "climate change", "climte change": "climate change",
    "artifical intelligence": "artificial intelligence",
    "machine lerning": "machine learning", "maching learning": "machine learning",
    "deep lerning": "deep learning",
    "disection": "dissection", "destilation": "distillation",
    "centrifuge": "centrifuge", "catalist": "catalyst",
    "molecular": "molecular", "moleculer": "molecular",
    "protien": "protein", "proten": "protein",
    "vitiman": "vitamin", "vitemin": "vitamin",
    "hormon": "hormone", "hormomes": "hormones",
    "sceince": "science", "sciense": "science",
    "relativity": "relativity", "relativty": "relativity",
    "quantum mechancis": "quantum mechanics",
    "fourier transform": "fourier transform", "fourier": "fourier",
    "eigenvalue": "eigenvalue", "eigen value": "eigenvalue",
    "derrivative": "derivative", "derivitive": "derivative",
    "integrl": "integral", "intergral": "integral",
    "derivatve": "derivative", "differential": "differential",
    "trignometry": "trigonometry", "trigonometry": "trigonometry",
    "geometry": "geometry", "geomety": "geometry",
    "speleology": "speleology", "spelelogist": "speleologist",
    "mycology": "mycology", "mycolgy": "mycology",
    "entomology": "entomology", "entomolgy": "entomology",
    "botny": "botany", "botony": "botany",
    "zology": "zoology", "zoolog": "zoology",
}

# ============================================================
# SYNONYM MAPS for query expansion
# ============================================================

SYNONYMS = {
    # Common knowledge queries
    "recipe": ["cooking", "food", "meal", "dish", "preparation"],
    "cooking": ["recipe", "food", "kitchen", "culinary", "meal"],
    "recipe": ["cooking", "food", "meal", "dish"],
    "algorithm": ["method", "procedure", "process", "technique"],
    "science": ["research", "study", "experiment", "investigation"],
    "math": ["mathematics", "calculation", "arithmetic", "numbers"],
    "history": ["past", "ancient", "historical", "heritage"],
    "philosophy": ["thought", "reasoning", "logic", "ethics"],
    "programming": ["coding", "development", "software", "developer"],
    "nutrition": ["diet", "food", "health", "vitamins", "minerals"],
    "medicine": ["health", "medical", "treatment", "therapy", "clinical"],
    "physics": ["mechanics", "thermodynamics", "electromagnetism", "quantum"],
    "chemistry": ["elements", "reactions", "compounds", "chemical"],
    "biology": ["life", "organisms", "genetics", "evolution", "ecology"],
    "economics": ["finance", "market", "trade", "business", "monetary"],
    "psychology": ["mind", "behavior", "cognitive", "mental", "neuroscience"],
    "geography": ["maps", "terrain", "climate", "countries", "regions"],
    "astronomy": ["space", "stars", "planets", "galaxy", "cosmos"],
    "ecology": ["environment", "ecosystem", "biodiversity", "conservation"],
    "language": ["linguistics", "grammar", "vocabulary", "grammar"],
    "architecture": ["building", "design", "structure", "construction"],
    "agriculture": ["farming", "growing", "crop", "harvest", "soil"],
    "engineering": ["design", "build", "construct", "mechanics", "systems"],
    "technology": ["computing", "digital", "electronic", "innovation"],
    "art": ["creative", "painting", "sculpture", "visual", "aesthetic"],
    "music": ["melody", "harmony", "rhythm", "composition", "instruments"],
    "religion": ["faith", "spirituality", "belief", "worship", "theology"],
    "education": ["learning", "teaching", "school", "curriculum", "pedagogy"],
    "legal": ["law", "justice", "court", "regulation", "statute"],
    "medical": ["health", "disease", "treatment", "diagnosis", "clinical"],
    "wildlife": ["animals", "fauna", "species", "habitat", "ecology"],
    "plant": ["flora", "garden", "botany", "botanical", "horticulture"],
    "ocean": ["marine", "sea", "aquatic", "underwater", "oceanography"],
    "climate": ["weather", "atmosphere", "temperature", "meteorology"],
}

# ============================================================
# STOP WORDS (smart — keeps meaningful words)
# ============================================================

STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "this",
    "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "me", "him", "her", "us", "them", "my", "your", "his", "its", "our",
    "their", "what", "which", "who", "whom", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "no", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "if", "about", "up", "out", "then", "here", "there",
    "also", "into", "over", "after", "before", "between", "under", "again",
}


# ============================================================
# CATEGORY KEYWORDS (for auto-categorization)
# ============================================================

CATEGORY_KEYWORDS = {
    "science_research": [
        "research", "study", "experiment", "hypothesis", "theory", "journal",
        "peer-reviewed", "paper", "scientific", "laboratory", "empirical",
    ],
    "programming_engineering": [
        "code", "programming", "software", "developer", "api", "database",
        "algorithm", "python", "javascript", "html", "css", "git", "linux",
    ],
    "university_learning": [
        "course", "lecture", "textbook", "curriculum", "syllabus", "exam",
        "university", "college", "degree", "semester", "class",
    ],
    "knowledge_foundations": [
        "encyclopedia", "history", "philosophy", "geography", "culture",
        "religion", "mythology", "art", "music", "literature", "language",
    ],
    "data_economics": [
        "data", "statistics", "economics", "finance", "market", "gdp",
        "demographics", "census", "survey", "chart", "graph", "dataset",
    ],
    "deep_thinking": [
        "logic", "reasoning", "argument", "debate", "analysis", "critique",
        "essay", "opinion", "perspective", "theory", "framework", "model",
    ],
}


class QueryProcessor:
    """
    Processes search queries through normalization, correction,
    expansion, and category inference.
    """

    def __init__(self):
        self.spelling = SPELLING_CORRECTIONS
        self.synonyms = SYNONYMS
        self.stop_words = STOP_WORDS

    def process(self, query: str) -> Dict:
        """
        Full query processing pipeline.
        Returns processed query data for the search engine.
        """
        original = query.strip()

        # Step 1: Normalize
        normalized = self._normalize(original)

        # Step 2: Spelling correction
        corrected = self._correct_spelling(normalized)

        # Step 3: Tokenize (keep meaningful words)
        tokens = self._tokenize(corrected)

        # Step 4: Expand with synonyms
        expanded = self._expand_synonyms(tokens)

        # Step 5: Detect category
        category = self._detect_category(tokens, original)

        # Step 6: Build search queries
        fts_query = self._build_fts_query(tokens)
        expanded_fts = self._build_fts_query(expanded)

        return {
            "original": original,
            "normalized": normalized,
            "corrected": corrected,
            "tokens": tokens,
            "expanded_tokens": expanded,
            "fts_query": fts_query,
            "expanded_fts": expanded_fts,
            "category_inferred": category,
            "spelling_corrected": normalized != corrected,
            "original_words": len(original.split()),
            "expanded_words": len(expanded),
        }

    def _normalize(self, query: str) -> str:
        """Normalize query text"""
        q = query.lower().strip()
        # Remove special characters but keep hyphens and apostrophes
        q = re.sub(r'[^\w\s\-\'\.]', ' ', q)
        # Normalize whitespace
        q = re.sub(r'\s+', ' ', q).strip()
        return q

    def _correct_spelling(self, query: str) -> str:
        """Correct common misspellings"""
        words = query.split()
        corrected = []
        changed = False

        for word in words:
            if word in self.spelling:
                corrected.append(self.spelling[word])
                changed = True
            elif len(word) > 3:
                # Try edit distance 1 correction
                best_match = self._closest_match(word)
                if best_match:
                    corrected.append(best_match)
                    changed = True
                else:
                    corrected.append(word)
            else:
                corrected.append(word)

        return " ".join(corrected)

    def _closest_match(self, word: str) -> Optional[str]:
        """Find closest matching word from known corrections"""
        best = None
        best_dist = float('inf')

        for known in self.spelling.values():
            d = self._edit_distance(word, known)
            if d < best_dist and d <= 2 and d < len(word) * 0.4:
                best_dist = d
                best = known

        return best

    def _edit_distance(self, s1: str, s2: str) -> int:
        """Levenshtein edit distance"""
        if len(s1) < len(s2):
            return self._edit_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        prev_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row

        return prev_row[-1]

    def _tokenize(self, query: str) -> List[str]:
        """Tokenize and remove stop words"""
        tokens = query.split()
        return [t for t in tokens if t not in self.stop_words and len(t) > 1]

    def _expand_synonyms(self, tokens: List[str]) -> List[str]:
        """Expand tokens with synonyms"""
        expanded = list(tokens)
        for token in tokens:
            if token in self.synonyms:
                # Add top 3 synonyms
                for syn in self.synonyms[token][:3]:
                    if syn not in expanded:
                        expanded.append(syn)
        return expanded

    def _detect_category(self, tokens: List[str], original: str) -> Optional[str]:
        """Auto-detect category from query content"""
        scores = {}
        text = " ".join(tokens).lower() + " " + original.lower()

        for category, keywords in CATEGORY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scores[category] = score

        if scores:
            return max(scores, key=scores.get)
        return None

    def _build_fts_query(self, tokens: List[str]) -> str:
        """Build FTS5-compatible query"""
        if not tokens:
            return ""
        return " OR ".join(tokens)


def process_query(query: str) -> Dict:
    """Convenience function"""
    processor = QueryProcessor()
    return processor.process(query)


# ============================================================
# SNIPPET GENERATION (for search results)
# ============================================================

def generate_snippet(content_text: str, query_tokens: List[str],
                     max_length: int = 300) -> str:
    """
    Generate a context-aware snippet around the query terms.
    Returns the most relevant paragraph/sentence.
    """
    if not content_text:
        return ""

    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', content_text)

    if not sentences:
        return content_text[:max_length]

    # Score each sentence by query term overlap
    scored = []
    query_set = set(t.lower() for t in query_tokens)

    for i, sent in enumerate(sentences):
        words = set(re.findall(r'\w+', sent.lower()))
        overlap = len(words & query_set)
        # Boost sentences near the start
        position_bonus = 1.0 / (1 + i * 0.1)
        score = overlap + position_bonus
        scored.append((score, i, sent))

    # Sort by score, take top 2-3 sentences
    scored.sort(reverse=True)
    top = scored[:3]
    top.sort(key=lambda x: x[1])  # Re-order by position

    snippet = " ".join(s for _, _, s in top)
    if len(snippet) > max_length:
        snippet = snippet[:max_length].rsplit(" ", 1)[0] + "..."

    return snippet
