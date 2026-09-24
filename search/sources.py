"""
Knowledge Sources — loaded from sources.json
Add/edit/remove sources in sources.json, this module reads it fast.
"""

import json
import os
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

_SOURCES_PATH = os.path.join(os.path.dirname(__file__), "sources.json")


class SourceCategory(Enum):
    KNOWLEDGE_FOUNDATIONS = "knowledge_foundations"
    SCIENCE_RESEARCH = "science_research"
    UNIVERSITY_LEARNING = "university_learning"
    PROGRAMMING_ENGINEERING = "programming_engineering"
    DATA_ECONOMICS = "data_economics"
    DEEP_THINKING = "deep_thinking"
    TECH_NEWS = "tech_news"
    HEALTH_MEDICAL = "health_medical"


@dataclass
class KnowledgeSource:
    name: str
    url: str
    category: SourceCategory
    crawl_type: str
    quality_score: float
    update_frequency: str
    has_api: bool = False
    api_endpoint: Optional[str] = None
    rate_limit: float = 1.0
    priority: int = 1


def _load() -> List[KnowledgeSource]:
    with open(_SOURCES_PATH, "r") as f:
        data = json.load(f)

    sources = []
    for s in data.get("sources", []):
        try:
            cat = SourceCategory(s["category"])
        except ValueError:
            cat = SourceCategory.KNOWLEDGE_FOUNDATIONS

        sources.append(KnowledgeSource(
            name=s["name"],
            url=s["url"],
            category=cat,
            crawl_type=s.get("crawl_type", "html"),
            quality_score=s.get("quality_score", 0.5),
            update_frequency=s.get("update_frequency", "weekly"),
            has_api=s.get("has_api", False),
            api_endpoint=s.get("api_endpoint"),
            rate_limit=s.get("rate_limit", 1.0),
            priority=s.get("priority", 2),
        ))
    return sources


ALL_SOURCES: List[KnowledgeSource] = _load()
SOURCE_MAP = {s.name: s for s in ALL_SOURCES}

SOURCES_BY_CATEGORY = {}
for _s in ALL_SOURCES:
    SOURCES_BY_CATEGORY.setdefault(_s.category.value, []).append(_s)

SOURCES_BY_CRAWL_TYPE = {}
for _s in ALL_SOURCES:
    SOURCES_BY_CRAWL_TYPE.setdefault(_s.crawl_type, []).append(_s)


def get_sources_by_category(category: SourceCategory) -> List[KnowledgeSource]:
    return SOURCES_BY_CATEGORY.get(category.value, [])


def get_sources_by_priority(priority: int) -> List[KnowledgeSource]:
    return [s for s in ALL_SOURCES if s.priority == priority]


def get_api_sources() -> List[KnowledgeSource]:
    return [s for s in ALL_SOURCES if s.has_api]


def get_source_stats() -> dict:
    return {
        "total": len(ALL_SOURCES),
        "by_category": {k: len(v) for k, v in SOURCES_BY_CATEGORY.items()},
        "by_crawl_type": {k: len(v) for k, v in SOURCES_BY_CRAWL_TYPE.items()},
        "by_priority": {p: len(get_sources_by_priority(p)) for p in range(1, 4)},
        "with_api": len(get_api_sources()),
        "high_quality": len([s for s in ALL_SOURCES if s.quality_score >= 0.95]),
    }
