#!/usr/bin/env python3
"""
Crawl diverse human knowledge sources and index into local SQLite.
Uses web_fetch via subprocess (calling the tool indirectly isn't possible,
so we'll do it inline with requests/httpx).
"""

import os
import sys
import re
import json
import hashlib
import logging
import time

# Setup paths
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("crawl_diverse")

from search.filters import SlopDetector
from search.indexers.turso_indexer import TursoGoldenLayer
from search.content_processor import calculate_content_quality

detector = SlopDetector()
layer = TursoGoldenLayer()
layer.create_tables()

# ============================================================
# Source definitions: (url, title, source_name, source_category, quality_score)
# ============================================================
SOURCES = [
    # Encyclopedias & Knowledge Bases
    ("https://citizendium.org", "Citizendium - The Citizen's Compendium", "Citizendium", "knowledge_foundations", 0.88),
    ("https://www.scholarpedia.org", "Scholarpedia - Peer-Reviewed Open Content Encyclopedia", "Scholarpedia", "knowledge_foundations", 0.93),
    ("https://everything2.com", "Everything2 - Knowledge Community", "Everything2", "knowledge_foundations", 0.85),

    # Programming & Developer
    ("https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide", "MDN JavaScript Guide", "MDN Web Docs", "programming_engineering", 0.95),
    ("https://developer.mozilla.org/en-US/docs/Learn/Getting_started_with_the_web", "MDN Getting Started with the Web", "MDN Web Docs", "programming_engineering", 0.95),
    ("https://docs.python.org/3/tutorial/index.html", "Python 3 Official Tutorial", "Python Docs", "programming_engineering", 0.94),
    ("https://go.dev/tour/", "A Tour of Go", "Go Tour", "programming_engineering", 0.92),
    ("https://www.rust-lang.org/learn", "Learn Rust Programming Language", "Rust Lang", "programming_engineering", 0.93),
    ("https://docs.rs/", "docs.rs - Rust Documentation", "docs.rs", "programming_engineering", 0.91),
    ("https://kotlinlang.org/docs/", "Kotlin Programming Language Documentation", "Kotlin Docs", "programming_engineering", 0.91),

    # Science & Research
    ("https://plato.stanford.edu/contents.html", "Stanford Encyclopedia of Philosophy - Table of Contents", "Stanford Encyclopedia", "deep_thinking", 0.96),
    ("https://www.nature.com/subjects/physics", "Nature Physics - Research Articles", "Nature", "science_research", 0.96),
    ("https://www.frontiersin.org/journals/physics", "Frontiers in Physics - Open Access Journal", "Frontiers", "science_research", 0.90),

    # Health & Medicine
    ("https://www.mayoclinic.org/diseases-conditions", "Mayo Clinic Diseases and Conditions", "Mayo Clinic", "science_research", 0.95),
    ("https://medlineplus.gov/encyclopedia.html", "MedlinePlus Medical Encyclopedia", "MedlinePlus", "science_research", 0.93),
    ("https://www.nhs.uk/conditions/", "NHS Conditions and Treatments", "NHS UK", "science_research", 0.93),

    # Law
    ("https://www.law.cornell.edu/wex", "Cornell Legal Information Institute - WEX", "Cornell LII", "deep_thinking", 0.94),

    # Mathematics
    ("https://mathworld.wolfram.com/", "MathWorld - Wolfram MathWorld", "MathWorld", "science_research", 0.93),
    ("https://oeis.org/", "OEIS - On-Line Encyclopedia of Integer Sequences", "OEIS", "science_research", 0.93),
    ("https://www.khanacademy.org/math", "Khan Academy Mathematics", "Khan Academy", "science_research", 0.93),

    # Open Education
    ("https://ocw.mit.edu/courses/", "MIT OpenCourseWare - Course Catalog", "MIT OpenCourseWare", "university_learning", 0.96),
    ("https://www.freecodecamp.org/learn", "freeCodeCamp Learn - Developer Curriculum", "FreeCodeCamp", "university_learning", 0.91),
    ("https://www.khanacademy.org/computing", "Khan Academy Computing and Programming", "Khan Academy", "university_learning", 0.93),

    # Food & Cooking
    ("https://www.seriouseats.com/", "Serious Eats - Food Knowledge and Cooking", "Serious Eats", "knowledge_foundations", 0.90),

    # Music & Arts
    ("https://imslp.org/wiki/Main_Page", "IMSLP - International Music Score Library Project", "IMSLP", "knowledge_foundations", 0.93),

    # Government Data
    ("https://data.gov/", "Data.gov - US Open Government Data", "Data.gov", "data_economics", 0.92),

    # Environment
    ("https://ourworldindata.org/", "Our World in Data - Global Knowledge", "Our World in Data", "data_economics", 0.95),
    ("https://www.epa.gov/", "US Environmental Protection Agency", "EPA", "science_research", 0.93),
]

# Track results
crawled = 0
indexed = 0
skipped_slop = 0
skipped_error = 0
categories = {}
sources_found = {}

print(f"\n{'='*60}")
print(f"  Diverse Human Knowledge Crawler")
print(f"  Sources: {len(SOURCES)}")
print(f"{'='*60}\n")

# We'll process each source. Since we can't call web_fetch from Python directly,
# we'll output the URLs and process results from the tool calls above.
# For now, let's create a script that processes pre-fetched content.

# Write source info for external processing
with open(os.path.join(os.path.dirname(__file__), "search/data/pending_sources.json"), "w") as f:
    json.dump(SOURCES, f, indent=2)

print("Source manifest written. Ready for processing.")
print(f"Total sources to crawl: {len(SOURCES)}")
