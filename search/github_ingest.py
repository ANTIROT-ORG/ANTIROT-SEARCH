"""
GitHub repository ingestion → standard crawled_gh_*.jsonl records.

Fetches top human-maintained repositories (via the GitHub Search API) and writes
them exactly like a crawl batch, so they flow through the whole pipeline:
quality signals → AI-slop gate → dedup → index → raw HuggingFace archive.

Usage:
    python -m search.github_ingest --query "topic:rust stars:>1000"
    GITHUB_TOKEN=... python -m search.github_ingest --topics "search-engine,information-retrieval"
    CRAWL_SKIP_CRAWL=true python -m search.pipeline          # index them
"""

import argparse
import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .crawlers import normalize_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("capybara.github_ingest")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
GITHUB_API = "https://api.github.com/search/repositories"
DEFAULT_TOPICS = ["search-engine", "information-retrieval",
                  "library", "documentation", "developer-tools"]

UA = "Antirot/2.0 (+https://github.com/BoringRats/ratcrowler)"


def build_repo_record(repo: dict, query: str, source_quality: float = 0.9) -> dict:
    """Map a GitHub API repo object to a crawl-shaped record."""
    from .crawlers import ContentScorer

    full_name = repo.get("full_name") or repo.get("name") or ""
    html_url = repo.get("html_url") or ""
    description = (repo.get("description") or "").strip()
    topics = ", ".join(repo.get("topics") or [])
    language = repo.get("language") or "unknown"
    license = (repo.get("license") or {}).get("spdx_id") or "unknown"
    stars = repo.get("stargazers_count") or 0

    # Build a distinctive content blob; also the label-keyword search targets
    content = (f"{full_name}: {description} "
               + f"Language {language} · License {license} · Topics {topics} "
               + f"Stars {stars} · fork {bool(repo.get('fork'))}" ).strip()
    if len(content) < 40:
        content = f"{full_name}: {description or 'No description provided.'}"
    if not html_url or not full_name:
        raise ValueError("repo missing url/name")

    # Authority scales with stars, capped at curated-source levels
    stars_authority = 0.5 + min(0.45, (min(stars, 50000) / 50000) * 0.45)
    quality = max(0.7, min(0.95, source_quality - 0.05 + (stars / 100000)))

    page = {
        "url": html_url,
        "title": full_name,
        "content_text": content,
        "content_html": "",
        "description": description[:200] or full_name,
        "author": (repo.get("owner") or {}).get("login") or "",
        "published_date": normalize_date(repo.get("created_at")),
        "language": "en",
        "word_count": len(content.split()),
        "heading_structure": {"h1": [full_name], "h2": [], "h3": []},
        "internal_links": [],
        "external_links": [],
        "media": [],
        "canonical_url": html_url,
        "robots_directives": {},
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "sitemap_priority": 0.8,
        "sitemap_changefreq": "weekly",
    }
    page.update(ContentScorer().score(page, quality))
    page["authority_score"] = round(stars_authority, 4)
    page["source_name"] = "GitHub"
    page["source_category"] = "programming_engineering"
    page["crawl_timestamp"] = datetime.now(timezone.utc).isoformat()
    page["_query"] = query
    page["stars"] = stars
    page["metadata"] = {"github_api": True, "stars": stars, "topics": topics, "query": query}
    return page


def _search(query: str, per_page: int = 100, token: str = "") -> list:
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    url = f"{GITHUB_API}?q={urllib.parse.quote(query)}&sort=stars&order=desc&per_page={per_page}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get("items", [])


def ingest(topics=DEFAULT_TOPICS, query: str = "", per_topic: int = 20, dry_run: bool = False) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    records = []
    seen = set()
    queries = [{"topic": t} for t in topics] if query is None or not query else [{"raw": query}]

    for group in queries:
        qs = query if query else f"topic:{group['topic']} stars:>500"
        items = _search(qs, per_page=per_topic, token=token)
        for repo in items:
            html_url = repo.get("html_url") or ""
            if html_url in seen:
                continue
            seen.add(html_url)
            try:
                records.append(build_repo_record(repo, qs))
            except ValueError:
                continue
        if len(records) >= per_topic * 4:
            break

    if not dry_run and records:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(DATA_DIR, f"crawled_gh_{stamp}.jsonl")
        with open(path, "w") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
    else:
        path = None

    logger.info("GitHub ingest: %d repo records (%s)", len(records),
                "dry run" if dry_run else f"wrote {path}")
    return {"records": len(records), "file": path, "dry_run": dry_run}


def main():
    parser = argparse.ArgumentParser(description="Ingest top GitHub repositories")
    parser.add_argument("--topics", default=",".join(DEFAULT_TOPICS),
                        help="comma-separated topics")
    parser.add_argument("--query", default="", help="single raw GitHub search query")
    parser.add_argument("--per-topic", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    stats = ingest(
        topics=[t.strip() for t in args.topics.split(",") if t.strip()],
        query=args.query.strip(), per_topic=args.per_topic, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()