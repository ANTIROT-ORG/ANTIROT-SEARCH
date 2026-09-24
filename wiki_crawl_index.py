#!/usr/bin/env python3
"""
Fetch all Wikipedia articles, process through SlopDetector, and index into SQLite.
"""

import sys
import os
import json
import re
import time
import hashlib
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from search.filters import SlopDetector
from search.content_processor import calculate_content_quality
from search.indexers.turso_indexer import TursoGoldenLayer
from datetime import datetime

URLS = [
    "https://en.wikipedia.org/wiki/Quantum_mechanics",
    "https://en.wikipedia.org/wiki/Theory_of_relativity",
    "https://en.wikipedia.org/wiki/Evolution",
    "https://en.wikipedia.org/wiki/DNA",
    "https://en.wikipedia.org/wiki/Climate_change",
    "https://en.wikipedia.org/wiki/Artificial_intelligence",
    "https://en.wikipedia.org/wiki/Machine_learning",
    "https://en.wikipedia.org/wiki/Algorithm",
    "https://en.wikipedia.org/wiki/Calculus",
    "https://en.wikipedia.org/wiki/Topology",
    "https://en.wikipedia.org/wiki/Ancient_Rome",
    "https://en.wikipedia.org/wiki/Renaissance",
    "https://en.wikipedia.org/wiki/World_War_II",
    "https://en.wikipedia.org/wiki/Internet",
    "https://en.wikipedia.org/wiki/Linux",
    "https://en.wikipedia.org/wiki/Philosophy",
    "https://en.wikipedia.org/wiki/Ethics",
    "https://en.wikipedia.org/wiki/Logic",
    "https://en.wikipedia.org/wiki/Astronomy",
    "https://en.wikipedia.org/wiki/Black_hole",
    "https://en.wikipedia.org/wiki/Solar_System",
    "https://en.wikipedia.org/wiki/Economics",
    "https://en.wikipedia.org/wiki/Psychology",
    "https://en.wikipedia.org/wiki/Neuroscience",
    "https://en.wikipedia.org/wiki/Medicine",
    "https://en.wikipedia.org/wiki/Genetics",
    "https://en.wikipedia.org/wiki/Cell_biology",
    "https://en.wikipedia.org/wiki/Ecology",
    "https://en.wikipedia.org/wiki/Chemistry",
    "https://en.wikipedia.org/wiki/Periodic_table",
    "https://en.wikipedia.org/wiki/Physics",
    "https://en.wikipedia.org/wiki/Thermodynamics",
    "https://en.wikipedia.org/wiki/Electromagnetism",
    "https://en.wikipedia.org/wiki/Biology",
    "https://en.wikipedia.org/wiki/Mathematics",
    "https://en.wikipedia.org/wiki/Geometry",
    "https://en.wikipedia.org/wiki/Probability",
    "https://en.wikipedia.org/wiki/Democracy",
    "https://en.wikipedia.org/wiki/Capitalism",
    "https://en.wikipedia.org/wiki/Architecture",
    "https://en.wikipedia.org/wiki/Photography",
    "https://en.wikipedia.org/wiki/Film",
    "https://en.wikipedia.org/wiki/Agriculture",
    "https://en.wikipedia.org/wiki/Mythology",
    "https://en.wikipedia.org/wiki/Space_exploration",
    "https://en.wikipedia.org/wiki/Renewable_energy",
    "https://en.wikipedia.org/wiki/Blockchain",
    "https://en.wikipedia.org/wiki/Cryptocurrency",
    "https://en.wikipedia.org/wiki/Data_science",
    "https://en.wikipedia.org/wiki/Disease",
    "https://en.wikipedia.org/wiki/Ancient_Egypt",
    "https://en.wikipedia.org/wiki/Scientific_method",
    "https://en.wikipedia.org/wiki/Peer_review",
    "https://en.wikipedia.org/wiki/Linguistics",
    "https://en.wikipedia.org/wiki/Sociology",
    "https://en.wikipedia.org/wiki/Anthropology",
    "https://en.wikipedia.org/wiki/Trigonometry",
    "https://en.wikipedia.org/wiki/Algebra",
    "https://en.wikipedia.org/wiki/Number_theory",
    "https://en.wikipedia.org/wiki/Combinatorics",
    "https://en.wikipedia.org/wiki/Electrical_engineering",
    "https://en.wikipedia.org/wiki/Civil_engineering",
    "https://en.wikipedia.org/wiki/Impressionism",
    "https://en.wikipedia.org/wiki/Classical_music",
    "https://en.wikipedia.org/wiki/Jazz",
    "https://en.wikipedia.org/wiki/Shakespeare",
    "https://en.wikipedia.org/wiki/Novel",
    "https://en.wikipedia.org/wiki/Poetry",
    "https://en.wikipedia.org/wiki/Climate",
    "https://en.wikipedia.org/wiki/Ocean",
    "https://en.wikipedia.org/wiki/Plate_tectonics",
    "https://en.wikipedia.org/wiki/Fossil_fuel",
    "https://en.wikipedia.org/wiki/Nuclear_energy",
    "https://en.wikipedia.org/wiki/Cloud_computing",
    "https://en.wikipedia.org/wiki/Internet_of_things",
    "https://en.wikipedia.org/wiki/Cancer",
    "https://en.wikipedia.org/wiki/Vaccine",
    "https://en.wikipedia.org/wiki/Antibiotics",
    "https://en.wikipedia.org/wiki/Human_rights",
    "https://en.wikipedia.org/wiki/Constitutional_law",
    "https://en.wikipedia.org/wiki/Macroeconomics",
    "https://en.wikipedia.org/wiki/Microeconomics",
    "https://en.wikipedia.org/wiki/Game_theory",
    "https://en.wikipedia.org/wiki/Cognitive_science",
    "https://en.wikipedia.org/wiki/Behaviorism",
    "https://en.wikipedia.org/wiki/Epidemiology",
    "https://en.wikipedia.org/wiki/Biochemistry",
    "https://en.wikipedia.org/wiki/Organic_chemistry",
    "https://en.wikipedia.org/wiki/Optics",
    "https://en.wikipedia.org/wiki/Taxonomy",
    "https://en.wikipedia.org/wiki/Nationalism",
    "https://en.wikipedia.org/wiki/Socialism",
    "https://en.wikipedia.org/wiki/Theatre",
    "https://en.wikipedia.org/wiki/Dance",
    "https://en.wikipedia.org/wiki/Fermentation",
    "https://en.wikipedia.org/wiki/Cooking",
    "https://en.wikipedia.org/wiki/Nutrition",
    "https://en.wikipedia.org/wiki/Greek_mythology",
    "https://en.wikipedia.org/wiki/Norse_mythology",
    "https://en.wikipedia.org/wiki/Mars",
    "https://en.wikipedia.org/wiki/Jupiter",
    "https://en.wikipedia.org/wiki/Saturn",
    "https://en.wikipedia.org/wiki/Galaxy",
    "https://en.wikipedia.org/wiki/Earth",
    "https://en.wikipedia.org/wiki/Amazon_rainforest",
    "https://en.wikipedia.org/wiki/Antarctica",
    "https://en.wikipedia.org/wiki/Great_Barrier_Reef",
    "https://en.wikipedia.org/wiki/Maya_civilization",
    "https://en.wikipedia.org/wiki/Indus_valley_civilization",
    "https://en.wikipedia.org/wiki/Open_access",
    "https://en.wikipedia.org/wiki/Big_data",
]

# Category mapping
CATEGORY_MAP = {
    "Philosophy": "knowledge_foundations", "Ethics": "knowledge_foundations",
    "Logic": "knowledge_foundations", "Scientific_method": "knowledge_foundations",
    "Peer_review": "knowledge_foundations", "Linguistics": "knowledge_foundations",
    "Sociology": "knowledge_foundations", "Anthropology": "knowledge_foundations",
    "Cognitive_science": "knowledge_foundations", "Behaviorism": "knowledge_foundations",
    "Democracy": "knowledge_foundations", "Socialism": "knowledge_foundations",
    "Nationalism": "knowledge_foundations", "Constitutional_law": "knowledge_foundations",
    "Human_rights": "knowledge_foundations", "Open_access": "knowledge_foundations",
    "Quantum_mechanics": "science_research", "Theory_of_relativity": "science_research",
    "Evolution": "science_research", "DNA": "science_research",
    "Climate_change": "science_research", "Physics": "science_research",
    "Thermodynamics": "science_research", "Electromagnetism": "science_research",
    "Biology": "science_research", "Chemistry": "science_research",
    "Genetics": "science_research", "Cell_biology": "science_research",
    "Ecology": "science_research", "Biochemistry": "science_research",
    "Organic_chemistry": "science_research", "Optics": "science_research",
    "Taxonomy": "science_research", "Neuroscience": "science_research",
    "Medicine": "science_research", "Disease": "science_research",
    "Cancer": "science_research", "Vaccine": "science_research",
    "Antibiotics": "science_research", "Epidemiology": "science_research",
    "Fermentation": "science_research", "Nutrition": "science_research",
    "Climate": "science_research", "Ocean": "science_research",
    "Plate_tectonics": "science_research", "Periodic_table": "science_research",
    "Fossil_fuel": "science_research", "Astronomy": "science_research",
    "Black_hole": "science_research", "Solar_System": "science_research",
    "Galaxy": "science_research", "Earth": "science_research",
    "Mars": "science_research", "Jupiter": "science_research",
    "Saturn": "science_research", "Space_exploration": "science_research",
    "Renewable_energy": "science_research", "Nuclear_energy": "science_research",
    "Artificial_intelligence": "programming_engineering",
    "Machine_learning": "programming_engineering", "Algorithm": "programming_engineering",
    "Internet": "programming_engineering", "Linux": "programming_engineering",
    "Blockchain": "programming_engineering", "Cryptocurrency": "programming_engineering",
    "Cloud_computing": "programming_engineering", "Internet_of_things": "programming_engineering",
    "Electrical_engineering": "programming_engineering", "Civil_engineering": "programming_engineering",
    "Calculus": "university_learning", "Topology": "university_learning",
    "Mathematics": "university_learning", "Geometry": "university_learning",
    "Probability": "university_learning", "Trigonometry": "university_learning",
    "Algebra": "university_learning", "Number_theory": "university_learning",
    "Combinatorics": "university_learning", "Game_theory": "university_learning",
    "Economics": "data_economics", "Macroeconomics": "data_economics",
    "Microeconomics": "data_economics", "Data_science": "data_economics",
    "Photography": "data_economics",
    "Ancient_Rome": "deep_thinking", "Renaissance": "deep_thinking",
    "World_War_II": "deep_thinking", "Greek_mythology": "deep_thinking",
    "Norse_mythology": "deep_thinking", "Mythology": "deep_thinking",
    "Ancient_Egypt": "deep_thinking", "Maya_civilization": "deep_thinking",
    "Indus_valley_civilization": "deep_thinking", "Amazon_rainforest": "deep_thinking",
    "Antarctica": "deep_thinking", "Great_Barrier_Reef": "deep_thinking",
    "Architecture": "deep_thinking", "Impressionism": "deep_thinking",
    "Classical_music": "deep_thinking", "Jazz": "deep_thinking",
    "Shakespeare": "deep_thinking", "Novel": "deep_thinking",
    "Poetry": "deep_thinking", "Film": "deep_thinking",
    "Theatre": "deep_thinking", "Dance": "deep_thinking",
    "Cooking": "deep_thinking", "Agriculture": "deep_thinking",
    "Psychology": "deep_thinking", "Capitalism": "deep_thinking",
}


def fetch_wikipedia_text(url):
    """Fetch Wikipedia article text via REST API."""
    slug = url.split("/wiki/")[-1]
    api_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
    
    try:
        req = urllib.request.Request(api_url, headers={
            "User-Agent": "RatCrowler/1.0 (research bot; contact@example.com)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            title = data.get("title", slug.replace("_", " "))
            extract = data.get("extract", "")
            return title, extract
    except Exception as e:
        print(f"  [WARN] Summary API failed for {slug}: {e}")
    
    # Fallback to text extract
    api_url2 = f"https://en.wikipedia.org/api/rest_v1/page/text/{slug}"
    try:
        req = urllib.request.Request(api_url2, headers={
            "User-Agent": "RatCrowler/1.0 (research bot; contact@example.com)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            title = data.get("title", slug.replace("_", " ", ))
            # Combine sections
            text_parts = []
            for section in data.get("sections", []):
                sec_text = section.get("text", "")
                if sec_text:
                    text_parts.append(sec_text)
            extract = " ".join(text_parts)
            return title, extract
    except Exception as e2:
        print(f"  [WARN] Text API also failed for {slug}: {e2}")
    
    # Final fallback: parse from HTML via Wikipedia API
    api_url3 = f"https://en.wikipedia.org/w/api.php?action=query&titles={slug}&prop=extracts&explaintext=true&format=json"
    try:
        req = urllib.request.Request(api_url3, headers={
            "User-Agent": "RatCrowler/1.0 (research bot; contact@example.com)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", {})
            for pid, page in pages.items():
                title = page.get("title", slug.replace("_", " "))
                extract = page.get("extract", "")
                return title, extract
    except Exception as e3:
        print(f"  [ERROR] All APIs failed for {slug}: {e3}")
        return slug.replace("_", " "), ""


def clean_extract(text, max_chars=5000):
    """Clean Wikipedia extract text."""
    if not text:
        return ""
    
    # Remove references like [1], [2], etc.
    text = re.sub(r'\[\d+\]', '', text)
    text = re.sub(r'\[edit\]', '', text)
    
    # Remove sections markers
    text = re.sub(r'\n\n==+.*?==+\n\n', '\n\n', text)
    text = re.sub(r'\n\n=+.*?=+\n\n', '\n\n', text)
    
    # Clean up whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    text = text.strip()
    
    # Truncate to max_chars
    if len(text) > max_chars:
        text = text[:max_chars]
        last_space = text.rfind(' ')
        if last_space > max_chars - 500:
            text = text[:last_space]
    
    return text


def main():
    print(f"=" * 60)
    print(f"Wikipedia Crawler & Indexer")
    print(f"Total articles to crawl: {len(URLS)}")
    print(f"=" * 60)
    
    # Initialize components
    detector = SlopDetector()
    indexer = TursoGoldenLayer()
    indexer.create_tables()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    jsonl_path = f"search/data/crawled_{timestamp}.jsonl"
    
    items = []
    skipped_slop = 0
    skipped_empty = 0
    skipped_error = 0
    
    for i, url in enumerate(URLS):
        slug = url.split("/wiki/")[-1]
        print(f"\n[{i+1}/{len(URLS)}] Fetching: {slug}...")
        
        # Fetch
        title, raw_text = fetch_wikipedia_text(url)
        
        if not raw_text or len(raw_text.strip()) < 100:
            skipped_empty += 1
            print(f"  [SKIP] Empty content")
            continue
        
        # Clean
        content_text = clean_extract(raw_text)
        
        if not content_text or len(content_text) < 100:
            skipped_empty += 1
            print(f"  [SKIP] Too short after cleaning")
            continue
        
        # Determine category
        source_category = CATEGORY_MAP.get(slug, "knowledge_foundations")
        word_count = len(content_text.split())
        
        # Create knowledge item
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
        
        # Run SlopDetector
        slop_result = detector.analyze(
            item["content_text"],
            source_name=item["source_name"],
            title=item["title"],
            url=item["url"]
        )
        
        if slop_result.is_slop:
            skipped_slop += 1
            print(f"  [SLOP] Detected (confidence: {slop_result.confidence:.3f})")
            continue
        
        items.append(item)
        print(f"  [OK] '{title}' ({word_count} words, cat: {source_category})")
        
        # Rate limit: 0.2s between requests
        time.sleep(0.2)
    
    # Save JSONL
    print(f"\n{'='*60}")
    print(f"Saving {len(items)} items to {jsonl_path}...")
    with open(jsonl_path, 'w') as f:
        for item in items:
            f.write(json.dumps(item) + '\n')
    print(f"Saved!")
    
    # Index to database
    print(f"\nIndexing to database...")
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
        else:
            print(f"  [FAIL] {item['title']}")
    
    # Rebuild FTS
    indexer.refresh_fts()
    
    # Stats
    stats = indexer.get_stats()
    print(f"\n{'='*60}")
    print(f"CRAWL & INDEX COMPLETE!")
    print(f"{'='*60}")
    print(f"  Articles crawled:     {len(URLS)}")
    print(f"  Successfully fetched: {len(items)}")
    print(f"  Skipped (empty):      {skipped_empty}")
    print(f"  Skipped (slop):       {skipped_slop}")
    print(f"  Indexed to DB:        {indexed}")
    print(f"  Total items in DB:    {stats['total_items']}")
    print(f"  Categories:           {stats['categories']}")
    print(f"  JSONL file:           {jsonl_path}")
    print(f"  DB file:              search/data/local.db")
    print(f"{'='*60}")
    
    # Print category breakdown
    cat_counts = {}
    for item in items:
        cat = item['source_category']
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    print(f"\nCategory breakdown:")
    for cat, count in sorted(cat_counts.items()):
        print(f"  {cat}: {count}")
    
    indexer.close()
    return indexed


if __name__ == "__main__":
    main()
