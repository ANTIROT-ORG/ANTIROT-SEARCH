#!/usr/bin/env python3
"""
Wikipedia Crawler — Fetch, process, and index Wikipedia articles.
"""

import sys
import os
import json
import re
import time
import hashlib
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from search.filters import SlopDetector
from search.content_processor import calculate_content_quality
from search.indexers.turso_indexer import TursoGoldenLayer

# Category mapping based on article topic
CATEGORY_MAP = {
    # knowledge_foundations
    "Philosophy": "knowledge_foundations",
    "Ethics": "knowledge_foundations",
    "Logic": "knowledge_foundations",
    "Scientific_method": "knowledge_foundations",
    "Peer_review": "knowledge_foundations",
    "Linguistics": "knowledge_foundations",
    "Sociology": "knowledge_foundations",
    "Anthropology": "knowledge_foundations",
    "Cognitive_science": "knowledge_foundations",
    "Behaviorism": "knowledge_foundations",
    "Democracy": "knowledge_foundations",
    "Socialism": "knowledge_foundations",
    "Nationalism": "knowledge_foundations",
    "Constitutional_law": "knowledge_foundations",
    "Human_rights": "knowledge_foundations",
    "Open_access": "knowledge_foundations",

    # science_research
    "Quantum_mechanics": "science_research",
    "Theory_of_relativity": "science_research",
    "Evolution": "science_research",
    "DNA": "science_research",
    "Climate_change": "science_research",
    "Physics": "science_research",
    "Thermodynamics": "science_research",
    "Electromagnetism": "science_research",
    "Biology": "science_research",
    "Chemistry": "science_research",
    "Genetics": "science_research",
    "Cell_biology": "science_research",
    "Ecology": "science_research",
    "Biochemistry": "science_research",
    "Organic_chemistry": "science_research",
    "Optics": "science_research",
    "Taxonomy": "science_research",
    "Neuroscience": "science_research",
    "Medicine": "science_research",
    "Disease": "science_research",
    "Cancer": "science_research",
    "Vaccine": "science_research",
    "Antibiotics": "science_research",
    "Epidemiology": "science_research",
    "Fermentation": "science_research",
    "Nutrition": "science_research",
    "Climate": "science_research",
    "Ocean": "science_research",
    "Plate_tectonics": "science_research",
    "Periodic_table": "science_research",
    "Fossil_fuel": "science_research",
    "Astronomy": "science_research",
    "Black_hole": "science_research",
    "Solar_System": "science_research",
    "Galaxy": "science_research",
    "Earth": "science_research",
    "Mars": "science_research",
    "Jupiter": "science_research",
    "Saturn": "science_research",
    "Space_exploration": "science_research",
    "Renewable_energy": "science_research",
    "Nuclear_energy": "science_research",

    # programming_engineering
    "Artificial_intelligence": "programming_engineering",
    "Machine_learning": "programming_engineering",
    "Algorithm": "programming_engineering",
    "Internet": "programming_engineering",
    "Linux": "programming_engineering",
    "Blockchain": "programming_engineering",
    "Cryptocurrency": "programming_engineering",
    "Cloud_computing": "programming_engineering",
    "Internet_of_things": "programming_engineering",
    "Electrical_engineering": "programming_engineering",
    "Civil_engineering": "programming_engineering",

    # university_learning
    "Calculus": "university_learning",
    "Topology": "university_learning",
    "Mathematics": "university_learning",
    "Geometry": "university_learning",
    "Probability": "university_learning",
    "Trigonometry": "university_learning",
    "Algebra": "university_learning",
    "Number_theory": "university_learning",
    "Combinatorics": "university_learning",
    "Game_theory": "university_learning",

    # data_economics
    "Economics": "data_economics",
    "Macroeconomics": "data_economics",
    "Microeconomics": "data_economics",
    "Data_science": "data_economics",
    "Photography": "data_economics",

    # deep_thinking
    "Ancient_Rome": "deep_thinking",
    "Renaissance": "deep_thinking",
    "World_War_II": "deep_thinking",
    "Greek_mythology": "deep_thinking",
    "Norse_mythology": "deep_thinking",
    "Mythology": "deep_thinking",
    "Ancient_Egypt": "deep_thinking",
    "Maya_civilization": "deep_thinking",
    "Indus_valley_civilization": "deep_thinking",
    "Amazon_rainforest": "deep_thinking",
    "Antarctica": "deep_thinking",
    "Great_Barrier_Reef": "deep_thinking",
    "Architecture": "deep_thinking",
    "Impressionism": "deep_thinking",
    "Classical_music": "deep_thinking",
    "Jazz": "deep_thinking",
    "Shakespeare": "deep_thinking",
    "Novel": "deep_thinking",
    "Poetry": "deep_thinking",
    "Film": "deep_thinking",
    "Theatre": "deep_thinking",
    "Dance": "deep_thinking",
    "Cooking": "deep_thinking",
    "Agriculture": "deep_thinking",
    "Psychology": "deep_thinking",
}


def extract_wiki_title_from_url(url: str) -> str:
    """Extract article title from Wikipedia URL."""
    # https://en.wikipedia.org/wiki/Quantum_mechanics -> Quantum_mechanics
    match = re.search(r'wiki/([^#?]+)', url)
    if match:
        return match.group(1).replace('_', ' ')
    return url.split('/')[-1].replace('_', ' ')


def extract_wiki_content(raw_text: str) -> str:
    """Extract clean article content from raw Wikipedia text."""
    if not raw_text:
        return ""

    lines = raw_text.split('\n')
    content_lines = []
    in_article = False
    skip_sections = {
        'references', 'see also', 'external links', 'notes',
        'further reading', 'citations', 'bibliography',
        'sources', 'footnotes', 'other websites'
    }

    for line in lines:
        stripped = line.strip()

        # Skip empty lines at the start
        if not in_article and not stripped:
            continue

        # Skip navigation/boilerplate
        if any(skip in stripped.lower() for skip in [
            'jump to navigation', 'jump to search', 'article',
            'talk', 'read', 'edit', 'view history',
            'wikipedia, the free encyclopedia',
            'from wikipedia', 'main article',
            'coordinates:', 'this article',
            'part of a series on',
            'short description',
            'citation:', 'dmy dates',
        ]):
            continue

        # Detect section headers
        if stripped.startswith('Contents') or stripped.startswith('Contents'):
            continue

        # Detect "References" or "See also" sections (end of article)
        lower_stripped = stripped.lower()
        if lower_stripped in skip_sections:
            break

        # Skip lines that are just navigation
        if stripped in ['Navigation menu', 'Personal tools', 'Namespaces',
                       'Views', 'More', 'Search', 'Contribute']:
            continue

        # Skip very short lines that look like navigation
        if len(stripped) < 3 and not stripped:
            continue

        # Skip reference markers like [1], [edit], etc. within lines
        # but keep the line if it has substantial content
        cleaned = re.sub(r'\[edit\]', '', stripped)
        cleaned = re.sub(r'\[\d+\]', '', cleaned)  # Remove citation numbers

        # Start capturing after we see substantial text
        if len(cleaned) > 40:
            in_article = True

        if in_article:
            content_lines.append(cleaned)

    content = ' '.join(content_lines)

    # Clean up multiple spaces
    content = re.sub(r'\s+', ' ', content).strip()

    return content


def create_knowledge_item(url: str, raw_text: str) -> dict:
    """Create a knowledge item from raw Wikipedia text."""
    # Extract title from URL
    title_slug = url.split('/wiki/')[-1] if '/wiki/' in url else url.split('/')[-1]
    title = title_slug.replace('_', ' ')

    # Extract clean content
    content_text = extract_wiki_content(raw_text)

    if not content_text or len(content_text) < 100:
        return None

    # Truncate to 5000 chars
    if len(content_text) > 5000:
        content_text = content_text[:5000]
        # Truncate at last complete word
        last_space = content_text.rfind(' ')
        if last_space > 4500:
            content_text = content_text[:last_space]

    # Determine category
    source_category = CATEGORY_MAP.get(title_slug, 'knowledge_foundations')

    word_count = len(content_text.split())

    item = {
        "url": url,
        "title": title,
        "content_text": content_text,
        "source_name": "Wikipedia",
        "source_category": source_category,
        "author": "Wikipedia Contributors",
        "published_date": "",
        "language": "en",
        "word_count": word_count,
        "quality_score": 0.95,
        "overall_rank": 0.95,
        "authority_score": 1.0,
        "freshness_score": 0.7,
        "engagement_score": 0.7,
        "quality_signals": calculate_content_quality({
            "content_text": content_text,
            "title": title,
            "heading_structure": {},
            "internal_links": [],
            "external_links": [],
            "media": [],
        }),
    }

    return item


def process_and_index(raw_texts_file: str):
    """Process crawled raw texts, run slop detection, save JSONL, and index to DB."""
    # Load raw texts
    with open(raw_texts_file, 'r') as f:
        raw_data = json.load(f)

    print(f"Loaded {len(raw_data)} raw articles")

    # Initialize
    detector = SlopDetector()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    jsonl_file = f"search/data/crawled_{timestamp}.jsonl"

    items = []
    skipped_slop = 0
    skipped_empty = 0

    for entry in raw_data:
        url = entry["url"]
        raw_text = entry.get("text", "")

        # Create knowledge item
        item = create_knowledge_item(url, raw_text)
        if not item:
            skipped_empty += 1
            print(f"  [SKIP] Empty content: {url}")
            continue

        # Run slop detection
        slop_result = detector.analyze(
            item["content_text"],
            source_name=item["source_name"],
            title=item["title"],
            url=item["url"]
        )

        if slop_result.is_slop:
            skipped_slop += 1
            print(f"  [SLOP] {item['title']} (confidence: {slop_result.confidence:.3f})")
            continue

        items.append(item)
        print(f"  [OK] {item['title']} ({item['word_count']} words, category: {item['source_category']})")

    # Save JSONL
    with open(jsonl_file, 'w') as f:
        for item in items:
            f.write(json.dumps(item) + '\n')

    print(f"\nSaved {len(items)} items to {jsonl_file}")
    print(f"Skipped: {skipped_slop} slop, {skipped_empty} empty")

    # Index to database
    print(f"\nIndexing to database...")
    indexer = TursoGoldenLayer()
    indexer.create_tables()

    indexed = 0
    for item in items:
        item_id = indexer.upsert_knowledge_item(item)
        if item_id:
            indexer.update_source_stats(
                item["source_name"],
                item["quality_score"],
                trusted=True
            )
            indexed += 1
            print(f"  [INDEXED] {item['title']} (id={item_id})")
        else:
            print(f"  [FAIL] {item['title']}")

    # Rebuild FTS
    indexer.refresh_fts()

    # Print stats
    stats = indexer.get_stats()
    print(f"\n{'='*50}")
    print(f"Indexing complete!")
    print(f"  Items indexed: {indexed}")
    print(f"  Total items in DB: {stats['total_items']}")
    print(f"  Categories: {stats['categories']}")
    print(f"  Top sources: {stats['top_sources']}")

    indexer.close()
    return indexed


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python wiki_crawler.py <raw_texts.json>")
        sys.exit(1)

    process_and_index(sys.argv[1])
