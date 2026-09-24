"""
Wikipedia dump ingestion via multistream range requests.

The multistream dump is a 26 GB bz2 file where every ~100 articles form an
independent bz2 stream. The companion index file maps offsets → articles, so
we can:

  1. Partially download + incrementally decompress the index (first N entries)
  2. Range-fetch only the byte ranges of the streams we need
  3. Parse `<page>` XML, clean wikitext into plain text
  4. Emit standard `crawled_wiki_*.jsonl` records for the normal pipeline

No API rate limits, no AI, no full-dump download.

CLI:
    python -m search.wiki_dump --limit 200
    python -m search.wiki_dump --limit 500 --lang en --refresh-index
"""

import argparse
import bz2
import hashlib
import html
import json
import logging
import os
import random
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("capybara.wiki_dump")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

DUMP_BASE = "https://dumps.wikimedia.org/{lang}wiki/latest/{lang}wiki-latest-pages-articles-multistream"
INDEX_URL = DUMP_BASE + "-index.txt.bz2"
DUMP_URL = DUMP_BASE + ".xml.bz2"

USER_AGENT = "Antirot/2.0 (+https://github.com/BoringRats/ratcrowler)"
INDEX_CACHE = os.path.join(DATA_DIR, "wiki_index_sample.jsonl")
INDEX_PARTIAL_BYTES = 4_000_000
STREAM_FETCH_STEPS = (1_500_000, 3_000_000, 6_000_000)
MAGIC_WINDOW = 2_000_000
SKIP_NAMESPACES = (
    "File:", "Image:", "Category:", "Wikipedia:", "Template:", "Help:",
    "Portal:", "Special:", "Talk:", "User:", "Draft:", "Module:", "MediaWiki:",
    "wikt:", "Wiktionary:", "s:", "q:",
)


# ============================================================
# INDEX
# ============================================================

def fetch_range(url: str, start: int, end: int, timeout: int = 60) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def dump_size(lang: str = "en") -> int:
    """Total compressed size of the multistream XML dump."""
    request = urllib.request.Request(
        DUMP_URL.format(lang=lang),
        method="HEAD",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return int(response.headers["Content-Length"])


def locate_magic(window: bytes) -> int:
    """Index of the first bz2 stream boundary ('BZh') in a byte window."""
    return window.find(b"BZh")


def parse_index_line(line: str) -> Optional[Tuple[int, int, str]]:
    """Parse `offset:pageid:title` (titles may contain colons)."""
    parts = line.split(":", 2)
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), parts[2].strip()
    except ValueError:
        return None


def load_index_sample(lang: str = "en", limit: int = 500,
                      refresh: bool = False) -> List[Tuple[int, int, str]]:
    """Load (or fetch) the first `limit` index entries."""
    if not refresh and os.path.exists(INDEX_CACHE):
        cached: List[Tuple[int, int, str]] = []
        with open(INDEX_CACHE) as fh:
            for line in fh:
                parsed = parse_index_line(line.strip())
                if parsed:
                    cached.append(parsed)
                if len(cached) >= limit:
                    return cached[:limit]
        if len(cached) >= limit:
            return cached[:limit]
        # Cached sample too small — fall through and fetch a larger one

    logger.info("Fetching partial Wikipedia index (%.1f MB)", INDEX_PARTIAL_BYTES / 1e6)
    raw = fetch_range(INDEX_URL.format(lang=lang), 0, INDEX_PARTIAL_BYTES)
    decompressor = bz2.BZ2Decompressor()
    text = decompressor.decompress(raw).decode("utf-8", "ignore")

    entries: List[Tuple[int, int, str]] = []
    for line in text.splitlines():
        parsed = parse_index_line(line)
        if parsed:
            entries.append(parsed)
        if len(entries) >= limit:
            break

    with open(INDEX_CACHE, "w") as fh:
        for offset, page_id, title in entries:
            fh.write(f"{offset}:{page_id}:{title}\n")
    logger.info(f"Cached {len(entries)} index entries")
    return entries


# ============================================================
# STREAM FETCH + PARSE
# ============================================================

def fetch_stream(offset: int, lang: str = "en") -> str:
    """Fetch and decompress one multistream bz2 stream (growing the range
    until the decompressed XML ends with a complete page or the cap is hit)."""
    url = DUMP_URL.format(lang=lang)
    decompressed = b""

    for size in STREAM_FETCH_STEPS:
        raw = fetch_range(url, offset, offset + size - 1)
        decompressor = bz2.BZ2Decompressor()
        try:
            decompressed = decompressor.decompress(raw)
        except Exception:
            continue
        if decompressor.eof or decompressed.rstrip().endswith(b"</page>"):
            break

    return decompressed.decode("utf-8", "ignore")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element, name: str) -> str:
    for child in element:
        if _localname(child.tag) == name:
            return child.text or ""
    return ""


def parse_stream(xml_text: str) -> List[Dict]:
    """Parse `<page>` records from a stream (streams lack the root element)."""
    if not xml_text.strip():
        return []

    wrapped = f"<mediawiki>{xml_text}</mediawiki>"
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError:
        # Truncated tail page → cut at the last complete page
        last_close = wrapped.rfind("</page>")
        if last_close == -1:
            return []
        wrapped = wrapped[:last_close + len("</page>")] + "</mediawiki>"
        try:
            root = ET.fromstring(wrapped)
        except ET.ParseError:
            return []

    pages: List[Dict] = []
    for page in root:
        if _localname(page.tag) != "page":
            continue
        if _child_text(page, "ns").strip() != "0":
            continue  # articles only

        revision = None
        redirect = False
        for child in page:
            if _localname(child.tag) == "redirect":
                redirect = True
            elif _localname(child.tag) == "revision":
                revision = child

        if redirect or revision is None:
            continue

        text = ""
        rev_timestamp = ""
        for child in revision:
            tag = _localname(child.tag)
            if tag == "timestamp":
                rev_timestamp = (child.text or "").strip()
            elif tag == "text":
                if child.get("deleted") is not None:
                    text = ""
                else:
                    text = child.text or ""

        if not text.strip():
            continue

        pages.append({
            "page_id": _child_text(page, "id").strip(),
            "title": _child_text(page, "title").strip(),
            "wikitext": text,
            "revision_timestamp": rev_timestamp,
        })

    return pages


# ============================================================
# WIKITEXT CLEANING (classical regex pipeline)
# ============================================================

def wikitext_to_text(wikitext: str) -> str:
    text = wikitext

    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<ref[^>]*/\s*>", "", text, flags=re.I)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    text = re.sub(r"<(gallery|timeline|math|score|syntaxhighlight)\b.*?</\1>", "", text,
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "", text)

    # Nested templates, innermost first
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)

    # Tables and files/categories
    text = re.sub(r"\{\|.*?\|\}", "", text, flags=re.S)
    text = re.sub(r"\[\[(?:File|Image|Category)\s*:[^\]]*\]\]", "", text, flags=re.I)

    # Links: [[target|label]] → label, [[target]] → target
    text = re.sub(r"\[\[(?:[^|\]]*)\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)

    # External links
    text = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", text)
    text = re.sub(r"\[https?://\S*\]", "", text)

    # Emphasis
    text = text.replace("'''", "").replace("''", "")

    # Headings, lists, indentation
    text = re.sub(r"^={1,6}\s*(.*?)\s*={1,6}\s*$", r"\1", text, flags=re.M)
    text = re.sub(r"^[#*:;]+\s*", "", text, flags=re.M)

    # Magic words and behavior switches
    text = re.sub(r"__[A-Z]+__", "", text)

    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_wiki_links(wikitext: str, lang: str = "en", limit: int = 80) -> List[str]:
    """Internal article links from wikitext, for the PageRank graph."""
    links: List[str] = []
    seen = set()

    for match in re.finditer(r"\[\[([^|\]#]+)(?:[|#][^\]]*)?\]\]", wikitext):
        target = match.group(1).strip()
        if not target or target.startswith(SKIP_NAMESPACES):
            continue
        title = target.replace(" ", "_")
        url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}"
        if url not in seen:
            seen.add(url)
            links.append(url)
        if len(links) >= limit:
            break

    return links


# ============================================================
# RECORD BUILDING
# ============================================================

def build_page(title: str, page_id: str, wikitext: str, lang: str = "en",
               revision_ts: str = "") -> Optional[Dict]:
    from .crawlers import ContentScorer, normalize_date

    content_text = wikitext_to_text(wikitext)
    if len(content_text) < 200:
        return None
    content_text = content_text[:45000]

    url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
    links = extract_wiki_links(wikitext, lang)

    page = {
        "url": url,
        "title": title,
        "content_text": content_text,
        "content_html": "",
        "description": content_text[:200],
        "author": "",
        "published_date": normalize_date(revision_ts),
        "language": lang,
        "word_count": len(content_text.split()),
        "heading_structure": {},
        "internal_links": links,
        "external_links": [],
        "media": [],
        "canonical_url": url,
        "robots_directives": {},
        "content_hash": hashlib.sha256(content_text.encode()).hexdigest(),
        "sitemap_priority": 0.9,
        "sitemap_changefreq": "monthly",
    }

    scores = ContentScorer().score(page, source_quality=0.98)
    page.update(scores)
    page["source_name"] = "Wikipedia"
    page["source_category"] = "knowledge_foundations"
    page["crawl_timestamp"] = datetime.now(timezone.utc).isoformat()
    page["metadata"] = {
        "source": "wikipedia_dump",
        "dump_page_id": page_id,
        "internal_link_count": len(links),
    }
    return page


# ============================================================
# INGEST
# ============================================================

def _write_pages(pages: List[Dict], output: Optional[str], prefix: str = "wiki") -> str:
    output = output or os.path.join(
        DATA_DIR,
        f"crawled_{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl",
    )
    with open(output, "w") as fh:
        for page in pages:
            fh.write(json.dumps(page, default=str) + "\n")
    return output


def find_stream_offset(probe: int, lang: str = "en",
                       window: int = MAGIC_WINDOW) -> Optional[int]:
    """Find the next bz2 stream boundary at or after `probe`."""
    data = fetch_range(DUMP_URL.format(lang=lang), probe, probe + window - 1)
    index = locate_magic(data)
    return probe + index if index != -1 else None


def random_stream_offsets(count: int, lang: str = "en",
                          seed: Optional[int] = None) -> List[int]:
    """Uniformly random, well-separated stream offsets across the dump."""
    rng = random.Random(seed)
    size = dump_size(lang)
    offsets: List[int] = []
    attempts = 0

    while len(offsets) < count and attempts < count * 4:
        attempts += 1
        probe = rng.randrange(1000, max(2000, size - MAGIC_WINDOW - 1))
        offset = find_stream_offset(probe, lang)
        if offset is None:
            continue
        if any(abs(offset - existing) < MAGIC_WINDOW for existing in offsets):
            continue
        offsets.append(offset)

    return offsets


def ingest_random(limit: int = 400, streams: int = 0, lang: str = "en",
                  seed: Optional[int] = None, output: Optional[str] = None) -> Dict:
    """
    Import uniformly random articles from anywhere in the dump.
    This is how the corpus stays diverse (the index cache only covers the
    pageid-ordered prefix of the corpus).
    """
    started = time.time()
    streams = streams or max(2, (limit // 40) + 1)
    offsets = random_stream_offsets(streams, lang=lang, seed=seed)
    if not offsets:
        return {"error": "no stream offsets found"}

    pages: List[Dict] = []
    skipped = 0

    for offset in offsets:
        if len(pages) >= limit:
            break
        xml_text = fetch_stream(offset, lang=lang)
        for parsed in parse_stream(xml_text):
            if len(pages) >= limit:
                break
            page = build_page(parsed["title"], parsed["page_id"], parsed["wikitext"], lang,
                              parsed.get("revision_timestamp", ""))
            if page:
                page["metadata"]["stream_offset"] = offset
                pages.append(page)
            else:
                skipped += 1
        logger.info(f"Stream {offset}: {len(pages)} articles so far")

    output = _write_pages(pages, output)

    return {
        "mode": "random",
        "seed": seed,
        "articles": len(pages),
        "skipped": skipped,
        "streams_fetched": len(offsets),
        "stream_offsets": offsets,
        "output_file": output,
        "duration_seconds": round(time.time() - started, 2),
    }


def ingest(limit: int = 200, lang: str = "en", refresh_index: bool = False,
           output: Optional[str] = None) -> Dict:
    started = time.time()
    # Oversample: redirects, non-article namespaces and stubs are skipped
    index = load_index_sample(lang=lang, limit=min(limit * 4, 20_000), refresh=refresh_index)
    if not index:
        return {"error": "empty index sample"}

    # Group titles by stream offset (consecutive index entries share offsets)
    streams: Dict[int, List[Tuple[int, str]]] = {}
    for offset, page_id, title in index:
        streams.setdefault(offset, []).append((page_id, title))

    pages: List[Dict] = []
    skipped = 0
    fetched_streams = 0

    for offset, wanted in streams.items():
        if len(pages) >= limit:
            break
        xml_text = fetch_stream(offset, lang=lang)
        fetched_streams += 1
        wanted_titles = {title for _id, title in wanted}

        for parsed in parse_stream(xml_text):
            if parsed["title"] not in wanted_titles:
                continue
            page = build_page(parsed["title"], parsed["page_id"], parsed["wikitext"], lang,
                              parsed.get("revision_timestamp", ""))
            if page:
                pages.append(page)
            else:
                skipped += 1
        logger.info(f"Stream {offset}: {len(pages)} articles so far")

    output = output or os.path.join(
        DATA_DIR, f"crawled_wiki_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
    )
    with open(output, "w") as fh:
        for page in pages:
            fh.write(json.dumps(page, default=str) + "\n")

    return {
        "articles": len(pages),
        "skipped": skipped,
        "streams_fetched": fetched_streams,
        "index_entries": len(index),
        "output_file": output,
        "duration_seconds": round(time.time() - started, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Antirot Wikipedia dump ingestion")
    parser.add_argument("--limit", type=int, default=200, help="articles to import")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--refresh-index", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--random", action="store_true",
                        help="sample uniformly random streams instead of the index prefix")
    parser.add_argument("--streams", type=int, default=0,
                        help="random streams to fetch (default: derived from --limit)")
    parser.add_argument("--seed", type=int, default=None, help="random seed")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.random:
        stats = ingest_random(
            limit=args.limit,
            streams=args.streams,
            lang=args.lang,
            seed=args.seed,
            output=args.output,
        )
    else:
        stats = ingest(
            limit=args.limit,
            lang=args.lang,
            refresh_index=args.refresh_index,
            output=args.output,
        )
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
