"""
Common Crawl ingestion — URL discovery via the CDX index + WARC range fetch.

Two stages:
  1. CDX API discovers archived URLs for a curated domain
     (status 200, text/html, deduplicated per URL key).
  2. WARC records are fetched with HTTP range requests from
     data.commoncrawl.org (no origin traffic), decompressed and parsed
     into the same page format as the live crawler.

Output lands in search/data/crawled_cc_*.jsonl so the normal
filter → index pipeline picks it up unchanged.

CLI:
    python -m search.cc_ingest --domain en.wikipedia.org --per-domain 20
    python -m search.cc_ingest --source Wikipedia --per-domain 50
"""

import argparse
import asyncio
import gzip
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("capybara.commoncrawl")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

CDX_ENDPOINT = "https://index.commoncrawl.org"
WARC_ENDPOINT = "https://data.commoncrawl.org"
COLLINFO_URL = f"{CDX_ENDPOINT}/collinfo.json"

USER_AGENT = "Antirot/2.0 (+https://github.com/BoringRats/ratcrowler)"
MAX_RECORD_BYTES = int(os.environ.get("RATSEARCH_CC_MAX_RECORD_BYTES", str(2_000_000)))
REQUEST_TIMEOUT = 30


# ============================================================
# CDX DISCOVERY
# ============================================================

def parse_cdx_line(line: str) -> Optional[Dict]:
    """Parse one JSONL line from the CDX API."""
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not raw.get("url") or not raw.get("filename"):
        return None

    return {
        "url": raw["url"],
        "timestamp": raw.get("timestamp", ""),
        "mime": (raw.get("mime") or "").lower(),
        "status": str(raw.get("status", "")),
        "digest": raw.get("digest", ""),
        "filename": raw["filename"],
        "offset": int(raw.get("offset") or 0),
        "length": int(raw.get("length") or 0),
        "encoding": raw.get("encoding", ""),
    }


async def latest_cc_index(session: aiohttp.ClientSession) -> str:
    """Return the most recent Common Crawl index id."""
    async with session.get(COLLINFO_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"collinfo.json returned {resp.status}")
        data = await resp.json(content_type=None)

    if not isinstance(data, list) or not data:
        raise RuntimeError("collinfo.json was empty")
    return data[0]["id"]


async def discover_urls(session: aiohttp.ClientSession, domain: str, index: str,
                        limit: int = 20, path_prefix: str = "") -> List[Dict]:
    """
    Query the CDX index for archived pages on a domain.

    Huge prefixes (e.g. en.wikipedia.org/*) often 504 on the CDX gateway;
    we retry with backoff and fall back to narrower patterns.
    """
    if path_prefix:
        patterns = [f"{domain}{path_prefix}"]
    else:
        patterns = [
            f"{domain}/*",
            f"{domain}/wiki/*",
            f"{domain}/docs/*",
            f"{domain}/en/*",
        ]

    for pattern in patterns:
        records = await _cdx_query(session, pattern, index, limit)
        if records:
            return records
    return []


async def _cdx_query(session: aiohttp.ClientSession, pattern: str, index: str,
                     limit: int) -> List[Dict]:
    params = [
        ("url", pattern),
        ("output", "json"),
        ("limit", str(limit)),
        ("filter", "status:200"),
        ("filter", "mime:text/html"),
        ("collapse", "urlkey"),
    ]
    url = f"{CDX_ENDPOINT}/{index}-index?" + "&".join(f"{k}={v}" for k, v in params)

    for attempt in range(3):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    records = [r for r in (parse_cdx_line(l) for l in text.splitlines()) if r]
                    return records
                logger.debug(f"CDX {pattern} attempt {attempt + 1}: HTTP {resp.status}")
        except Exception as e:
            logger.debug(f"CDX {pattern} attempt {attempt + 1} failed: {e}")
        await asyncio.sleep(1.5 * (attempt + 1))

    return []


# ============================================================
# WARC FETCH + PARSE
# ============================================================

def dechunk(body: bytes) -> bytes:
    """Decode HTTP chunked transfer encoding."""
    output = bytearray()
    position = 0
    while position < len(body):
        line_end = body.find(b"\r\n", position)
        if line_end == -1:
            break
        size_line = body[position:line_end].split(b";", 1)[0].strip()
        try:
            size = int(size_line, 16)
        except ValueError:
            break
        if size == 0:
            break
        start = line_end + 2
        output.extend(body[start:start + size])
        position = start + size + 2
    return bytes(output)


def parse_warc_response(data: bytes) -> Optional[Tuple[str, str]]:
    """
    Parse a gzipped WARC response record.
    Returns (content_type, html) or None.
    """
    try:
        raw = gzip.decompress(data)
    except Exception:
        return None

    warc_sep = raw.find(b"\r\n\r\n")
    if warc_sep == -1:
        return None

    warc_headers = raw[:warc_sep].decode("utf-8", "ignore")
    if "WARC-Type: response" not in warc_headers:
        return None

    payload = raw[warc_sep + 4:]
    http_sep = payload.find(b"\r\n\r\n")
    if http_sep == -1:
        return None

    http_headers = payload[:http_sep].decode("utf-8", "ignore").lower()
    body = payload[http_sep + 4:]

    # noindex in the archived response → respect it
    if "x-robots-tag" in http_headers and "noindex" in http_headers:
        return None

    if "transfer-encoding: chunked" in http_headers:
        decoded = dechunk(body)
        if decoded:
            body = decoded

    # Common Crawl sometimes stores the body already decoded while keeping
    # the original content-encoding header — always trust the magic bytes.
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:
            pass

    content_type = ""
    for line in http_headers.splitlines():
        if line.startswith("content-type:"):
            content_type = line.split(":", 1)[1].split(";")[0].strip()
            break

    if "text/html" not in content_type and "xhtml" not in content_type:
        return None

    try:
        return content_type, body.decode("utf-8", "ignore")
    except Exception:
        return None


async def fetch_warc_record(session: aiohttp.ClientSession, record: Dict,
                            semaphore: asyncio.Semaphore) -> Optional[str]:
    """Range-fetch and parse one WARC record; returns HTML or None."""
    if record["length"] <= 0 or record["length"] > MAX_RECORD_BYTES:
        return None

    start = record["offset"]
    end = start + record["length"] - 1
    url = f"{WARC_ENDPOINT}/{record['filename']}"

    async with semaphore:
        try:
            async with session.get(
                url,
                headers={"Range": f"bytes={start}-{end}"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status not in (200, 206):
                    return None
                data = await resp.read()
        except Exception as e:
            logger.debug(f"WARC fetch failed for {record['url']}: {e}")
            return None

    parsed = parse_warc_response(data)
    if not parsed:
        return None
    _content_type, html = parsed
    return html


# ============================================================
# INGEST PIPELINE
# ============================================================

def _domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().lstrip("www.")


def _source_for_domain(domain: str):
    from .sources import ALL_SOURCES, KnowledgeSource, SourceCategory

    for source in ALL_SOURCES:
        if _domain_of(source.url) == domain:
            return source

    return KnowledgeSource(
        name=domain,
        url=f"https://{domain}",
        category=SourceCategory.KNOWLEDGE_FOUNDATIONS,
        crawl_type="common_crawl",
        quality_score=0.7,
        update_frequency="monthly",
        priority=3,
    )


async def ingest(domains: List[str], per_domain: int = 20, index: Optional[str] = None,
                 concurrency: int = 6) -> Dict:
    from .crawlers import KnowledgeCrawler
    from .dedup import simhash64

    started = time.time()
    headers = {"User-Agent": USER_AGENT}
    semaphore = asyncio.Semaphore(concurrency)
    extractor = KnowledgeCrawler(max_concurrent=1)
    pages: List[Dict] = []
    per_source: Dict[str, Dict] = {}

    async with aiohttp.ClientSession(headers=headers) as session:
        if not index:
            index = await latest_cc_index(session)
        logger.info(f"Using Common Crawl index {index}")

        for domain in domains:
            discovered = await discover_urls(session, domain, index, limit=per_domain)
            source = _source_for_domain(domain)

            htmls = await asyncio.gather(*[
                fetch_warc_record(session, record, semaphore) for record in discovered
            ])

            fetched = 0
            seen_hashes = set()
            for record, html in zip(discovered, htmls):
                if not html:
                    continue
                try:
                    page = extractor._extract_page(html, record["url"], source, {}, [])
                except Exception as e:
                    logger.debug(f"Extract failed for {record['url']}: {e}")
                    continue
                if not page:
                    continue
                if page["content_hash"] in seen_hashes:
                    continue
                seen_hashes.add(page["content_hash"])
                page["metadata"]["common_crawl"] = {
                    "index": index,
                    "timestamp": record["timestamp"],
                    "digest": record["digest"],
                }
                page["crawl_timestamp"] = datetime.now(timezone.utc).isoformat()
                pages.append(page)
                fetched += 1

            per_source[source.name] = {"discovered": len(discovered), "fetched": fetched}
            logger.info(f"CC {domain}: {fetched}/{len(discovered)} pages")
            await asyncio.sleep(1)  # CDX politeness

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(DATA_DIR, f"crawled_cc_{timestamp}.jsonl")
    with open(output_file, "w") as f:
        for page in pages:
            item = {k: v for k, v in page.items() if k != "content_html"}
            f.write(json.dumps(item, default=str) + "\n")

    stats = {
        "index": index,
        "domains": len(domains),
        "total_pages": len(pages),
        "per_source": per_source,
        "output_file": output_file,
        "duration_seconds": round(time.time() - started, 2),
    }
    logger.info(f"Common Crawl ingest: {len(pages)} pages from {len(domains)} domains")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Antirot Common Crawl ingestion")
    parser.add_argument("--domain", action="append", default=[], help="domain to ingest (repeatable)")
    parser.add_argument("--source", action="append", default=[], help="curated source name (repeatable)")
    parser.add_argument("--per-domain", type=int, default=20)
    parser.add_argument("--index", default=None, help="CC index id (default: latest)")
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    domains = list(args.domain)
    if args.source:
        from .sources import SOURCE_MAP
        for name in args.source:
            source = SOURCE_MAP.get(name)
            if source:
                domains.append(_domain_of(source.url))
            else:
                print(f"Unknown source: {name}")

    if not domains:
        parser.error("provide --domain and/or --source")

    stats = asyncio.run(ingest(
        domains=list(dict.fromkeys(domains)),
        per_domain=args.per_domain,
        index=args.index,
        concurrency=args.concurrency,
    ))

    print(json.dumps({k: v for k, v in stats.items() if k != "per_source"}, indent=2, default=str))
    for name, result in stats["per_source"].items():
        print(f"  {name}: {result['fetched']}/{result['discovered']} pages")


if __name__ == "__main__":
    main()
