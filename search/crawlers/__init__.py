"""
Antirot Crawler — Production web crawler
Handles: robots.txt, sitemap.xml, content extraction, media discovery, scoring
"""

import asyncio
import aiohttp
import hashlib
import json
import os
import re
import time
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, asdict
from urllib.parse import urljoin, urlparse, urlunparse

logger = logging.getLogger("capybara.crawler")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ── Crawl budgets (env-tunable) ──
MAX_SITEMAPS_PER_SOURCE = int(os.environ.get("RATSEARCH_MAX_SITEMAPS", "10"))
MAX_SITEMAP_ENTRIES = int(os.environ.get("RATSEARCH_MAX_SITEMAP_ENTRIES", "2000"))
MAX_PAGES_PER_SOURCE = int(os.environ.get("RATSEARCH_MAX_PAGES_PER_SOURCE", "100"))
MAX_PAGES_PER_RUN = int(os.environ.get("RATSEARCH_MAX_PAGES_PER_RUN", "4000"))
MAX_SITEMAP_DEPTH = 2


# ============================================================
# PUBLISHED-DATE EXTRACTION (powers freshness + the `when` filter)
# ============================================================

# Meta tags that carry a publication date, then ones that only carry an edit date
_DATE_META_PUBLISHED = [
    ("property", "article:published_time"),
    ("property", "article:pubdate"),
    ("property", "og:published_time"),
    ("name", "date"),
    ("name", "pubdate"),
    ("name", "dcterms.date"),
    ("name", "DC.date"),
]
_DATE_META_MODIFIED = [
    ("property", "article:modified_time"),
    ("property", "og:updated_time"),
]


def normalize_date(raw: Optional[str]) -> str:
    """Normalize a date-ish string to YYYY-MM-DD; '' when unparseable.

    Handles ISO 8601 (with time/offset/Z), YYYY/MM/DD and RFC 2822
    (HTTP `Last-Modified` / sitemap email-style dates).
    """
    if not raw or not isinstance(raw, str):
        return ""
    value = raw.strip()

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        y, mo, d = m.groups()
        if 1900 <= int(y) <= 2100 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
        return ""

    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", value)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
        return ""

    try:
        dt = parsedate_to_datetime(value)
        if dt:
            return dt.date().isoformat()
    except Exception:
        pass
    return ""


def jsonld_date_candidates(soup) -> List[str]:
    """Publication-date candidates from JSON-LD, preferred order.

    MUST be called before <script> tags are stripped from the soup.
    """
    published: List[str] = []
    created: List[str] = []
    modified: List[str] = []
    for sc in soup.find_all("script"):
        if (sc.get("type") or "").split(";")[0].strip().lower() != "application/ld+json":
            continue
        try:
            data = json.loads(sc.get_text() or "")
        except Exception:
            continue
        nodes: List = []
        stack: List = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                nodes.append(node)
                graph = node.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(node, list):
                stack.extend(node)
        for node in nodes:
            if isinstance(node.get("datePublished"), str):
                published.append(node["datePublished"])
            if isinstance(node.get("dateCreated"), str):
                created.append(node["dateCreated"])
            if isinstance(node.get("dateModified"), str):
                modified.append(node["dateModified"])
    return published + created + modified


def resolve_date(candidates) -> str:
    """First candidate that normalizes to YYYY-MM-DD."""
    for candidate in candidates:
        normalized = normalize_date(candidate)
        if normalized:
            return normalized
    return ""


def freshness_for_date(published: Optional[str]) -> float:
    """Age buckets for the ranking blend — single source of truth
    (ContentScorer and backfill_dates.py both use this)."""
    if not published:
        return 0.5

    normalized = normalize_date(published)
    if normalized:
        try:
            pub = datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_old = (datetime.now(timezone.utc) - pub).days
            if days_old < 30:
                return 1.0
            elif days_old < 180:
                return 0.8
            elif days_old < 365:
                return 0.6
            elif days_old < 730:
                return 0.4
            return 0.3
        except ValueError:
            pass

    year_match = re.search(r"(20\d{2})", published)
    if year_match:
        age = datetime.now().year - int(year_match.group(1))
        return max(0.1, 1.0 - (age * 0.1))
    return 0.5


def _clean_feed_text(value) -> str:
    """Strip HTML/tags from an RSS/Atom text field."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    return re.sub(r"\s+", " ", text).strip()


def feed_entry_to_record(entry, source_name: str, source_category: str,
                         source_quality: float = 0.9) -> Optional[Dict]:
    """Turn an RSS/Atom entry into a crawl record shaped like _extract_page.

    Pure and testable — used by the RSS crawl branch (tech news, journals,
    Reddit trends, YouTube channels), so every item flows through the same
    slop gate, dedup, indexing and HF raw archive as a normal crawl.
    """
    url = str(entry.get("link") or "").strip()
    title = _clean_feed_text(entry.get("title") or "")
    raw_content = entry.get("content")
    if isinstance(raw_content, list) and raw_content:
        content = _clean_feed_text(raw_content[0].get("value"))
    else:
        content = _clean_feed_text(entry.get("summary") or "")
    if not url or not title or len(content) < 20:
        return None  # skip empty items; the slop gate judges thin ones next

    text = content[:45000]
    page = {
        "url": url,
        "title": title,
        "content_text": text,
        "content_html": "",
        "description": content[:200],
        "author": _clean_feed_text(entry.get("author") or "").strip(),
        "published_date": normalize_date(entry.get("published") or entry.get("updated")),
        "language": "en",
        "word_count": len(text.split()),
        "heading_structure": {"h1": [title], "h2": [], "h3": []},
        "internal_links": [],
        "external_links": [],
        "media": [],
        "canonical_url": url,
        "robots_directives": {},
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "sitemap_priority": 0.7,
        "sitemap_changefreq": "daily",
    }
    page.update(ContentScorer().score(page, source_quality))
    page["source_name"] = source_name
    page["source_category"] = source_category
    page["crawl_timestamp"] = datetime.now(timezone.utc).isoformat()
    page["metadata"] = {"feed_item": True}
    return page


@dataclass
class MediaItem:
    url: str
    media_type: str  # image, video, audio, document
    mime_type: str
    alt_text: str
    width: Optional[int]
    height: Optional[int]


@dataclass
class CrawledPage:
    url: str
    title: str
    source_name: str
    source_category: str
    content_text: str
    content_html: str
    description: str
    author: str
    published_date: str
    language: str
    word_count: int
    heading_structure: Dict  # {h1: [...], h2: [...], h3: [...]}
    internal_links: List[str]
    external_links: List[str]
    media: List[Dict]
    canonical_url: str
    robots_directives: Dict
    content_hash: str
    quality_score: float
    authority_score: float
    freshness_score: float
    engagement_score: float
    overall_rank: float
    crawl_timestamp: str
    sitemap_priority: float
    sitemap_changefreq: str
    metadata: Dict


# ============================================================
# ROBOTS.TXT PARSER
# ============================================================

class RobotsParser:
    """Parse and respect robots.txt — proper UA groups, wildcards, allow override"""

    # Legacy token kept forever: sites may have `User-agent: RatSearch` groups
    USER_AGENT_TOKEN = "RatSearch"

    def __init__(self):
        self._cache: Dict[str, Dict] = {}

    async def fetch_robots(self, session: aiohttp.ClientSession, base_url: str) -> Dict:
        parsed = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        if robots_url in self._cache:
            return self._cache[robots_url]

        try:
            async with session.get(robots_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    rules = self._parse_robots(text)
                    self._cache[robots_url] = rules
                    return rules
        except Exception:
            pass

        default = {"disallow": [], "allow": [], "sitemap": [], "crawl_delay": 1.0, "found": False}
        self._cache[robots_url] = default
        return default

    def _parse_robots(self, text: str) -> Dict:
        """Parse groups and select the one matching our UA, falling back to '*'."""
        groups: List[Dict] = []
        current = None
        expecting_agent = True

        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue

            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()

            if key == "user-agent":
                if current is None or not expecting_agent:
                    current = {"agents": [], "rules": []}
                    groups.append(current)
                current["agents"].append(value.lower())
                expecting_agent = True
            elif key == "sitemap":
                if current is None:
                    current = {"agents": [], "rules": []}
                    groups.append(current)
                current["rules"].append(("sitemap", value))
            elif key in ("allow", "disallow", "crawl-delay"):
                if current is None:
                    current = {"agents": ["*"], "rules": []}
                    groups.append(current)
                current["rules"].append((key, value))
                expecting_agent = False

        # Sitemaps are global regardless of group
        rules = {"disallow": [], "allow": [], "sitemap": [], "crawl_delay": 1.0, "found": True}
        for group in groups:
            for k, v in group["rules"]:
                if k == "sitemap" and v:
                    rules["sitemap"].append(v)

        token = self.USER_AGENT_TOKEN.lower()
        selected = next(
            (g for g in groups if any(token in a for a in g["agents"])),
            None,
        )
        if selected is None:
            selected = next((g for g in groups if "*" in g["agents"]), None)

        if selected:
            for k, v in selected["rules"]:
                if k in ("allow", "disallow"):
                    if v:
                        rules[k].append(v)
                elif k == "crawl-delay":
                    try:
                        rules["crawl_delay"] = max(1.0, float(v))
                    except ValueError:
                        pass

        return rules

    def can_fetch(self, rules: Dict, url: str) -> bool:
        """Longest-match wins: Allow overrides Disallow at equal or greater length."""
        parsed = urlparse(url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query

        best_len = -1
        allowed = True

        for pattern in rules.get("disallow", []):
            if self._matches(pattern, target):
                match_len = self._match_len(pattern)
                if match_len > best_len:
                    best_len = match_len
                    allowed = False

        for pattern in rules.get("allow", []):
            if self._matches(pattern, target):
                match_len = self._match_len(pattern)
                if match_len >= best_len:
                    best_len = match_len
                    allowed = True

        return allowed

    @staticmethod
    def _match_len(pattern: str) -> int:
        return len(pattern.rstrip("$").replace("*", ""))

    @staticmethod
    def _matches(pattern: str, target: str) -> bool:
        if not pattern:
            return False
        if pattern == "/":
            return True

        anchored = pattern.endswith("$")
        core = pattern[:-1] if anchored else pattern
        regex = re.escape(core).replace(r"\*", ".*")
        regex = "^" + regex + ("$" if anchored else "")
        return bool(re.match(regex, target))


# ============================================================
# SITEMAP.XML PARSER
# ============================================================

class SitemapParser:
    """Discover and parse sitemaps with depth and size guards"""

    def __init__(self):
        self._cache: Dict[str, List[Dict]] = {}
        self._visited: Set[str] = set()

    async def discover_sitemaps(self, session: aiohttp.ClientSession, base_url: str,
                                 robots_rules: Dict) -> List[Dict]:
        """Find sitemaps from robots.txt and common locations"""
        sitemap_urls = list(dict.fromkeys(robots_rules.get("sitemap", [])))

        # Common sitemap locations
        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        for path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
                      "/sitemap.txt", "/wp-sitemap.xml"]:
            url = base + path
            if url not in sitemap_urls:
                sitemap_urls.append(url)

        self._visited = set()
        entries: List[Dict] = []
        for sm_url in sitemap_urls[:MAX_SITEMAPS_PER_SOURCE]:
            if len(entries) >= MAX_SITEMAP_ENTRIES:
                break
            try:
                parsed_entries = await self._parse_sitemap(session, sm_url, depth=0)
                entries.extend(parsed_entries)
            except Exception as e:
                logger.debug(f"Failed to parse sitemap {sm_url}: {e}")

        # Deduplicate by URL, keep first seen
        seen: Set[str] = set()
        unique: List[Dict] = []
        for entry in entries:
            url = entry.get("url")
            if url and url not in seen:
                seen.add(url)
                unique.append(entry)
        return unique[:MAX_SITEMAP_ENTRIES]

    async def _parse_sitemap(self, session: aiohttp.ClientSession, sitemap_url: str,
                             depth: int = 0) -> List[Dict]:
        if sitemap_url in self._cache:
            return self._cache[sitemap_url]
        if sitemap_url in self._visited or depth > MAX_SITEMAP_DEPTH:
            return []
        self._visited.add(sitemap_url)

        try:
            async with session.get(sitemap_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []

                text = await resp.text()

                # Check if it's a sitemap index
                if "<sitemapindex" in text.lower():
                    return await self._parse_sitemap_index(session, text, sitemap_url, depth)

                entries = self._parse_sitemap_urls(text)
                self._cache[sitemap_url] = entries
                return entries
        except Exception:
            return []

    async def _parse_sitemap_index(self, session: aiohttp.ClientSession, text: str,
                                    base_url: str, depth: int = 0) -> List[Dict]:
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(text)
            locs: List[str] = []
            for sitemap in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap"):
                loc = sitemap.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
                if loc is not None and loc.text:
                    locs.append(loc.text.strip())
            if not locs:
                return []
            return await self._crawl_child_sitemaps(session, locs, depth)
        except Exception:
            # Fallback to regex if XML parsing fails
            urls = re.findall(r"<loc>(.*?)</loc>", text, re.IGNORECASE)
            return await self._crawl_child_sitemaps(session, urls, depth)

    async def _crawl_child_sitemaps(self, session: aiohttp.ClientSession,
                                     locs: List[str], depth: int) -> List[Dict]:
        entries: List[Dict] = []
        for loc in locs[:MAX_SITEMAPS_PER_SOURCE]:
            if len(entries) >= MAX_SITEMAP_ENTRIES:
                break
            child_entries = await self._parse_sitemap(session, loc, depth + 1)
            entries.extend(child_entries)
        return entries

    def _parse_sitemap_urls(self, text: str) -> List[Dict]:
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            entries = []
            for url_elem in root.findall(".//sm:url", ns):
                loc = url_elem.find("sm:loc", ns)
                if loc is None or not loc.text:
                    continue
                lastmod = url_elem.find("sm:lastmod", ns)
                priority = url_elem.find("sm:priority", ns)
                changefreq = url_elem.find("sm:changefreq", ns)
                entries.append({
                    "url": loc.text.strip(),
                    "lastmod": lastmod.text.strip() if lastmod is not None and lastmod.text else "",
                    "priority": float(priority.text) if priority is not None and priority.text else 0.5,
                    "changefreq": changefreq.text.strip() if changefreq is not None and changefreq.text else "weekly",
                })
            return entries
        except Exception:
            # Fallback to regex
            entries = []
            url_blocks = re.findall(r"<url>(.*?)</url>", text, re.IGNORECASE | re.DOTALL)
            for block in url_blocks:
                loc = re.search(r"<loc>(.*?)</loc>", block, re.IGNORECASE)
                lastmod = re.search(r"<lastmod>(.*?)</lastmod>", block, re.IGNORECASE)
                priority = re.search(r"<priority>(.*?)</priority>", block, re.IGNORECASE)
                changefreq = re.search(r"<changefreq>(.*?)</changefreq>", block, re.IGNORECASE)
                if loc:
                    entries.append({
                        "url": loc.group(1).strip(),
                        "lastmod": lastmod.group(1).strip() if lastmod else "",
                        "priority": float(priority.group(1)) if priority else 0.5,
                        "changefreq": changefreq.group(1).strip() if changefreq else "weekly",
                    })
            return entries


# ============================================================
# CONTENT SCORER — Google-like ranking
# ============================================================

class ContentScorer:
    """
    Scores content like Google's ranking factors:
    1. Content quality (depth, readability, uniqueness)
    2. Authority (source reputation, backlinks)
    3. Freshness (publication date, update frequency)
    4. Engagement signals (word count, media, structure)
    5. Technical SEO (title, meta, headings, canonical)
    """

    TRUSTED_DOMAINS = {
        "wikipedia.org": 1.0, "britannica.com": 0.98, "stanford.edu": 0.97,
        "arxiv.org": 0.96, "pubmed.ncbi.nlm.nih.gov": 0.97,
        "github.com": 0.90, "stackoverflow.com": 0.92,
        "nature.com": 0.98, "science.org": 0.98,
        "mit.edu": 0.97, "harvard.edu": 0.97, "yale.edu": 0.96,
        "mozilla.org": 0.95, "developer.mozilla.org": 0.96,
        "docs.python.org": 0.95, "go.dev": 0.94,
        "loc.gov": 0.98, "nasa.gov": 0.97,
        "gutenberg.org": 0.95, "archive.org": 0.95,
        "ourworldindata.org": 0.96, "worldbank.org": 0.95,
        "fred.stlouisfed.org": 0.96, "data.gov": 0.93,
        "pewresearch.org": 0.94, "aeon.co": 0.93,
        "quantamagazine.org": 0.95, "fs.blog": 0.93,
        "lesswrong.com": 0.91, "news.ycombinator.com": 0.88,
        "khanacademy.org": 0.95, "ocw.mit.edu": 0.97,
    }

    def score(self, page: Dict, source_quality: float = 0.5) -> Dict:
        content = page.get("content_text", "")
        title = page.get("title", "")
        url = page.get("url", "")

        quality = self._content_quality_score(content, title)
        authority = self._authority_score(url, source_quality)
        freshness = self._freshness_score(page)
        engagement = self._engagement_score(page)
        technical = self._technical_score(page)

        # Weighted final score (Google-like weighting)
        overall = (
            0.35 * quality +
            0.25 * authority +
            0.15 * freshness +
            0.15 * engagement +
            0.10 * technical
        )

        return {
            "quality_score": round(quality, 4),
            "authority_score": round(authority, 4),
            "freshness_score": round(freshness, 4),
            "engagement_score": round(engagement, 4),
            "overall_rank": round(overall, 4),
        }

    def _content_quality_score(self, content: str, title: str) -> float:
        if not content:
            return 0.0

        score = 0.0
        words = content.split()
        word_count = len(words)

        # Word count scoring (longer, deeper content ranks higher)
        if word_count > 3000:
            score += 0.3
        elif word_count > 1000:
            score += 0.25
        elif word_count > 500:
            score += 0.2
        elif word_count > 200:
            score += 0.15
        elif word_count > 50:
            score += 0.1

        # Readability: average sentence length
        sentences = re.split(r'[.!?]+', content)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
        if sentences:
            avg_sent_len = sum(len(s.split()) for s in sentences) / len(sentences)
            if 12 <= avg_sent_len <= 25:
                score += 0.2  # Good readability
            elif 8 <= avg_sent_len <= 35:
                score += 0.1

        # Vocabulary diversity
        unique_words = set(w.lower() for w in words if len(w) > 2)
        if word_count > 0:
            diversity = len(unique_words) / word_count
            if diversity > 0.6:
                score += 0.2
            elif diversity > 0.4:
                score += 0.15
            elif diversity > 0.25:
                score += 0.1

        # Title quality
        if title and len(title.split()) >= 3:
            score += 0.1

        # Presence of numbers, citations (sign of factual content)
        if re.search(r'\d{4}', content):  # Year references
            score += 0.05
        if re.search(r'\[\d+\]|\(\d{4}\)', content):  # Citations
            score += 0.1

        return min(score, 1.0)

    def _authority_score(self, url: str, source_quality: float) -> float:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Check trusted domains
        for trusted, score in self.TRUSTED_DOMAINS.items():
            if trusted in domain:
                return score

        # Base authority from source config
        return source_quality

    def _freshness_score(self, page: Dict) -> float:
        return freshness_for_date(page.get("published_date", ""))

    def _engagement_score(self, page: Dict) -> float:
        score = 0.0

        # Word count
        wc = page.get("word_count", 0)
        if wc > 1500:
            score += 0.25
        elif wc > 500:
            score += 0.2
        elif wc > 200:
            score += 0.15

        # Media presence
        media = page.get("media", [])
        if len(media) > 5:
            score += 0.2
        elif len(media) > 2:
            score += 0.15
        elif len(media) > 0:
            score += 0.1

        # Heading structure
        headings = page.get("heading_structure", {})
        h2_count = len(headings.get("h2", []))
        if h2_count >= 3:
            score += 0.2
        elif h2_count >= 1:
            score += 0.1

        # Internal links (sign of well-structured site)
        internal = len(page.get("internal_links", []))
        if internal > 5:
            score += 0.15
        elif internal > 2:
            score += 0.1

        return min(score, 1.0)

    def _technical_score(self, page: Dict) -> float:
        score = 0.0

        if page.get("title"):
            score += 0.2
        if page.get("description"):
            score += 0.15
        if page.get("canonical_url"):
            score += 0.1
        if page.get("language"):
            score += 0.05

        headings = page.get("heading_structure", {})
        if headings.get("h1"):
            score += 0.2
        if headings.get("h2"):
            score += 0.1

        if page.get("author"):
            score += 0.1

        robots = page.get("robots_directives", {})
        if not robots.get("noindex"):
            score += 0.1

        return min(score, 1.0)


# ============================================================
# MAIN CRAWLER
# ============================================================

class KnowledgeCrawler:
    """Production crawler with robots.txt, sitemap, media discovery, scoring"""

    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.session: Optional[aiohttp.ClientSession] = None
        self.robots = RobotsParser()
        self.sitemap = SitemapParser()
        self.scorer = ContentScorer()
        self.state: Dict[str, Dict] = {}
        self._seen_hashes: Set[str] = set()
        self._load_state()

    def _load_state(self):
        """Load per-URL crawl state (content hash, ETag, Last-Modified)."""
        path = os.path.join(DATA_DIR, "crawl_state.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    self.state = json.load(f)
            except Exception:
                self.state = {}

    def _save_state(self):
        with open(os.path.join(DATA_DIR, "crawl_state.json"), "w") as f:
            json.dump(self.state, f)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            # Low-compute connection pooling: keep-alive reuse, DNS caching, bounded sockets
            connector = aiohttp.TCPConnector(
                limit=30,
                limit_per_host=4,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
            )
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=20, connect=5),
                headers={
                    "User-Agent": "Antirot/2.0 (+https://github.com/BoringRats/ratcrowler)",
                    "Accept": "text/html,application/xml,application/json",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _crawl_feed(self, source) -> List[Dict]:
        """RSS/Atom ingestion (tech news, journals, Reddit, YouTube channels).

        Uses `api_endpoint` as the feed URL when set, else the source URL.
        Feed items become normal crawl records (dates included) so they flow
        through the slop gate, dedup, index and raw HF archive unchanged.
        """
        import feedparser

        feed_url = source.api_endpoint or source.url
        results: List[Dict] = []
        try:
            session = await self._get_session()
            async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status != 200:
                    logger.warning(f"Feed {feed_url}: HTTP {resp.status}")
                    return results
                body = await resp.text(errors="replace")

            parsed = feedparser.parse(body)
            seen: Set[str] = set()
            for entry in parsed.entries[:MAX_PAGES_PER_SOURCE]:
                url = (entry.get("link") or "").strip()
                if url in seen or not url:
                    continue
                seen.add(url)
                rec = feed_entry_to_record(entry, source.name,
                                           source.category.value, source.quality_score)
                if rec:
                    results.append(rec)
            logger.info(f"Feed {source.name}: {len(results)} items")
        except Exception as e:
            logger.error(f"Feed crawl error {source.name}: {e}")
        return results

    async def crawl_source(self, source) -> List[Dict]:
        """Full crawl pipeline for a source: robots → sitemap → content → score.

        RSS/Atom sources take the feed branch; everything else uses the
        robots → sitemap → HTML page pipeline.
        """
        if source.crawl_type == "rss":
            return await self._crawl_feed(source)

        session = await self._get_session()
        results = []

        try:
            # Step 1: Fetch robots.txt
            robots_rules = await self.robots.fetch_robots(session, source.url)
            crawl_delay = robots_rules.get("crawl_delay", 1.0)

            # Step 2: Discover pages from sitemaps
            sitemap_entries = await self.sitemap.discover_sitemaps(session, source.url, robots_rules)

            # Step 3: Add the source URL itself
            urls_to_crawl = [source.url]
            for entry in sitemap_entries[:MAX_SITEMAP_ENTRIES]:
                url = entry["url"]
                if not url.startswith(("http://", "https://")):
                    continue
                if self.robots.can_fetch(robots_rules, url):
                    urls_to_crawl.append(url)
                if len(urls_to_crawl) >= MAX_PAGES_PER_SOURCE:
                    break

            # Step 4: Crawl URLs concurrently within semaphore limit
            async def _crawl_one(url):
                try:
                    return await self._crawl_page(session, url, source, robots_rules, sitemap_entries)
                except Exception as e:
                    logger.debug(f"Crawl error {url}: {e}")
                    return ("error", None)

            tasks = [_crawl_one(u) for u in urls_to_crawl[:MAX_PAGES_PER_SOURCE]]
            outcomes = await asyncio.gather(*tasks)
            for status, page in outcomes:
                if status == "page" and page:
                    results.append(page)

            await asyncio.sleep(crawl_delay)

        except Exception as e:
            logger.error(f"Source crawl error {source.name}: {e}")

        return results

    async def _crawl_page(self, session: aiohttp.ClientSession, url: str,
                           source, robots_rules: Dict, sitemap_entries: List[Dict]) -> tuple:
        """Fetch one page with conditional GET and change detection.

        Returns (status, page): page | unchanged | not_modified | skipped | error
        """
        prev = self.state.get(url, {})
        headers = {}
        if prev.get("etag"):
            headers["If-None-Match"] = prev["etag"]
        if prev.get("last_modified"):
            headers["If-Modified-Since"] = prev["last_modified"]

        async with self.semaphore:
            async with session.get(url, allow_redirects=True, headers=headers) as resp:
                if resp.status == 304:
                    self.state[url] = {**prev, "crawled_at": datetime.utcnow().isoformat()}
                    return ("not_modified", None)

                if resp.status != 200:
                    return ("skipped", None)

                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    return ("skipped", None)

                # Bounded read (max 1MB) prevents memory exhaustion from bloated pages
                raw_bytes = await resp.content.read(1024 * 1024)
                encoding = resp.charset or "utf-8"
                try:
                    html = raw_bytes.decode(encoding, errors="replace")
                except Exception:
                    html = raw_bytes.decode("utf-8", errors="replace")

                page = self._extract_page(html, url, source, robots_rules, sitemap_entries)
                if not page:
                    return ("skipped", None)

                content_hash = page["content_hash"]

                # Duplicate content seen elsewhere in this run
                if content_hash in self._seen_hashes:
                    return ("unchanged", None)
                self._seen_hashes.add(content_hash)

                self.state[url] = {
                    "content_hash": content_hash,
                    "etag": resp.headers.get("ETag", ""),
                    "last_modified": resp.headers.get("Last-Modified", ""),
                    "crawled_at": datetime.utcnow().isoformat(),
                }

                if prev.get("content_hash") == content_hash:
                    return ("unchanged", None)

                return ("page", page)

    def _extract_page(self, html: str, url: str, source, robots_rules: Dict,
                       sitemap_entries: List[Dict]) -> Optional[Dict]:
        """Extract all data from HTML"""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return None

        soup = BeautifulSoup(html, "html.parser")

        # Date candidates from JSON-LD FIRST — the noise loop below deletes scripts
        date_candidates = jsonld_date_candidates(soup)

        # Remove noise
        for tag in soup.find_all(["script", "style", "noscript", "iframe",
                                  "nav", "footer", "header", "aside"]):
            tag.decompose()

        # ---- Title ----
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # ---- Meta ----
        meta_desc = soup.find("meta", attrs={"name": "description"})
        description = meta_desc.get("content", "") if meta_desc else ""

        meta_author = soup.find("meta", attrs={"name": "author"})
        author = meta_author.get("content", "") if meta_author else ""

        # Publication-date candidates: JSON-LD → meta published → <time> → meta edited.
        # (resolved to YYYY-MM-DD after the sitemap entry is known, below)
        for attr, value in _DATE_META_PUBLISHED:
            el = soup.find("meta", attrs={attr: value})
            if el and el.get("content"):
                date_candidates.append(el["content"])
        time_el = soup.find("time", attrs={"datetime": True})
        if time_el and time_el.get("datetime"):
            date_candidates.append(time_el["datetime"])
        for attr, value in _DATE_META_MODIFIED:
            el = soup.find("meta", attrs={attr: value})
            if el and el.get("content"):
                date_candidates.append(el["content"])

        canonical = soup.find("link", attrs={"rel": "canonical"})
        canonical_url = canonical.get("href", "") if canonical else ""

        robots_meta = soup.find("meta", attrs={"name": "robots"})
        robots_directives = {}
        if robots_meta:
            content = robots_meta.get("content", "").lower()
            robots_directives["noindex"] = "noindex" in content
            robots_directives["nofollow"] = "nofollow" in content

        # Respect noindex — skip this page entirely
        if robots_directives.get("noindex"):
            return None

        lang_tag = soup.find("html")
        language = lang_tag.get("lang", "en") if lang_tag else "en"

        # ---- Headings ----
        heading_structure = {}
        for level in ["h1", "h2", "h3"]:
            tags = soup.find_all(level)
            heading_structure[level] = [t.get_text(strip=True) for t in tags if t.get_text(strip=True)]

        # ---- Main Content ----
        main = (soup.find("article") or soup.find("main") or
                soup.find("div", {"role": "main"}) or soup.body)
        if main is None:
            return None

        content_text = re.sub(r'\s+', ' ', main.get_text(separator=" ", strip=True))
        if len(content_text) < 80:
            return None

        content_html = str(main)[:30000]

        # ---- Content Hash (change detection happens in _crawl_page) ----
        content_hash = hashlib.sha256(content_text.encode()).hexdigest()

        # ---- Links ----
        parsed_base = urlparse(url)
        internal_links = []
        external_links = []

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            full_url = urljoin(url, href)
            link_parsed = urlparse(full_url)

            if link_parsed.scheme not in ("http", "https"):
                continue

            # Respect nofollow on individual links
            rel = a_tag.get("rel", [])
            is_nofollow = "nofollow" in rel or robots_directives.get("nofollow", False)

            if is_nofollow:
                continue  # Don't count nofollow links

            if link_parsed.netloc == parsed_base.netloc:
                internal_links.append(full_url)
            else:
                external_links.append(full_url)

        # ---- Media Discovery ----
        media = []

        # Images
        seen_img_urls = set()

        # 1. High-quality OpenGraph and Twitter feature images
        for meta_prop in ["og:image", "og:image:url", "twitter:image", "twitter:image:src"]:
            meta_tag = soup.find("meta", attrs={"property": meta_prop}) or soup.find("meta", attrs={"name": meta_prop})
            if meta_tag and meta_tag.get("content"):
                img_url = urljoin(url, meta_tag["content"].strip())
                if img_url.startswith(("http://", "https://")) and img_url not in seen_img_urls:
                    seen_img_urls.add(img_url)
                    media.append({
                        "url": img_url,
                        "type": "image",
                        "alt": title or description or "",
                        "caption": description[:180] if description else "",
                        "is_featured": True,
                        "width": None,
                        "height": None,
                    })

        # 2. In-content images with srcset, caption, and dimensions (NO binary downloads)
        for img in soup.find_all("img"):
            src = None
            srcset = img.get("srcset") or img.get("data-srcset")
            if srcset:
                parts = [p.strip().split() for p in srcset.split(",") if p.strip()]
                if parts:
                    src = parts[-1][0]
            if not src:
                src = img.get("src") or img.get("data-src") or img.get("data-original")

            if not src or src.startswith("data:image/svg+xml") or src.startswith("data:image/gif"):
                continue

            img_url = urljoin(url, src)
            if not img_url.startswith(("http://", "https://")) or img_url in seen_img_urls:
                continue

            # Filter tracking pixels and tiny decorative icons
            w = img.get("width")
            h = img.get("height")
            try:
                if w and int(re.sub(r'\D', '', str(w))) < 32:
                    continue
                if h and int(re.sub(r'\D', '', str(h))) < 32:
                    continue
            except Exception:
                pass

            fig = img.find_parent("figure")
            caption = ""
            if fig:
                figcaption = fig.find("figcaption")
                if figcaption:
                    caption = figcaption.get_text(strip=True)

            seen_img_urls.add(img_url)
            media.append({
                "url": img_url,
                "type": "image",
                "alt": img.get("alt", "").strip() or caption or title or "",
                "caption": caption,
                "width": w,
                "height": h,
            })
            if len(media) >= 30:
                break

        # Videos
        for video in soup.find_all(["video", "source"]):
            src = video.get("src")
            if src:
                media.append({
                    "url": urljoin(url, src),
                    "type": "video",
                    "alt": "",
                    "width": None,
                    "height": None,
                })

        # Embeds (YouTube, Vimeo, etc.)
        for iframe in soup.find_all("iframe", src=True):
            src = iframe["src"]
            if any(yt in src for yt in ["youtube.com", "youtu.be", "vimeo.com"]):
                media.append({
                    "url": src,
                    "type": "video_embed",
                    "alt": iframe.get("title", ""),
                    "width": iframe.get("width"),
                    "height": iframe.get("height"),
                })

        # Documents (PDF, etc.)
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].lower()
            if href.endswith((".pdf", ".doc", ".docx", ".ppt", ".xlsx")):
                media.append({
                    "url": urljoin(url, a_tag["href"]),
                    "type": "document",
                    "alt": a_tag.get_text(strip=True),
                    "width": None,
                    "height": None,
                })

        # ---- Sitemap info ----
        sitemap_entry = next((e for e in sitemap_entries if e["url"] == url), {})
        published_date = resolve_date(date_candidates)
        if not published_date and sitemap_entry:
            published_date = normalize_date(sitemap_entry.get("lastmod", ""))

        # ---- Score everything ----
        page_data = {
            "url": url,
            "title": title,
            "content_text": content_text,
            "content_html": content_html,
            "description": description,
            "author": author,
            "published_date": published_date,
            "language": language,
            "word_count": len(content_text.split()),
            "heading_structure": heading_structure,
            "internal_links": list(set(internal_links))[:50],
            "external_links": list(set(external_links))[:50],
            "media": media,
            "canonical_url": canonical_url,
            "robots_directives": robots_directives,
            "content_hash": content_hash,
            "sitemap_priority": sitemap_entry.get("priority", 0.5),
            "sitemap_changefreq": sitemap_entry.get("changefreq", "weekly"),
        }

        scores = self.scorer.score(page_data, source.quality_score)
        page_data.update(scores)
        page_data["source_name"] = source.name
        page_data["source_category"] = source.category.value
        page_data["crawl_timestamp"] = datetime.utcnow().isoformat()
        page_data["metadata"] = {
            "robots_txt_compliant": True,
            "from_sitemap": bool(sitemap_entry),
            "sitemap_lastmod": sitemap_entry.get("lastmod", ""),
        }

        return page_data

    async def run(self, category: Optional[str] = None, priority: int = 1,
                  source_names: Optional[List[str]] = None) -> Dict:
        from ..sources import ALL_SOURCES, SourceCategory

        start = time.time()
        self._seen_hashes = set()

        if source_names is not None:
            wanted = set(source_names)
            sources = [s for s in ALL_SOURCES if s.name in wanted]
        elif category and category != "all":
            try:
                cat = SourceCategory(category)
                sources = [s for s in ALL_SOURCES if s.category == cat and s.priority <= priority]
            except ValueError:
                sources = [s for s in ALL_SOURCES if s.priority <= priority]
        else:
            sources = [s for s in ALL_SOURCES if s.priority <= priority]

        sources.sort(key=lambda s: (s.priority, -s.quality_score))

        all_pages: List[Dict] = []
        errors = []
        per_source: Dict[str, Dict] = {}
        budget_hit = False

        for source in sources:
            if len(all_pages) >= MAX_PAGES_PER_RUN:
                budget_hit = True
                logger.info(f"Run budget reached ({MAX_PAGES_PER_RUN} pages) — stopping")
                break

            pages = []
            source_errors = 0
            try:
                pages = await self.crawl_source(source)
                all_pages.extend(pages)
                logger.info(f"✓ {source.name}: {len(pages)} pages")
            except Exception as e:
                source_errors = 1
                errors.append({"source": source.name, "error": str(e)})
                logger.error(f"✗ {source.name}: {e}")

            per_source[source.name] = {
                "pages": len(pages),
                "avg_quality": round(
                    sum(p.get("quality_score", 0) for p in pages) / max(len(pages), 1), 3
                ),
                "errors": source_errors,
            }

            await asyncio.sleep(source.rate_limit)

        # Save results
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(DATA_DIR, f"crawled_{timestamp}.jsonl")
        with open(output_file, "w") as f:
            for page in all_pages:
                # Remove HTML content from saved file (too large for raw storage)
                save_item = {k: v for k, v in page.items() if k != "content_html"}
                f.write(json.dumps(save_item, default=str) + "\n")

        self._save_state()

        # Sort by overall rank
        all_pages.sort(key=lambda p: p.get("overall_rank", 0), reverse=True)

        stats = {
            "total_sources": len(sources),
            "crawled_sources": len(sources) - len(errors),
            "total_pages": len(all_pages),
            "state_tracked_urls": len(self.state),
            "budget_hit": budget_hit,
            "per_source": per_source,
            "errors": errors,
            "avg_quality": round(sum(p.get("quality_score", 0) for p in all_pages) / max(len(all_pages), 1), 3),
            "avg_rank": round(sum(p.get("overall_rank", 0) for p in all_pages) / max(len(all_pages), 1), 3),
            "media_total": sum(len(p.get("media", [])) for p in all_pages),
            "duration_seconds": round(time.time() - start, 2),
            "output_file": output_file,
        }

        with open(os.path.join(DATA_DIR, "crawl_stats.json"), "w") as f:
            json.dump(stats, f, indent=2, default=str)

        return stats
