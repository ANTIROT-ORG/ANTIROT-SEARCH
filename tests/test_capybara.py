"""
Antirot (formerly Capybara / RatSearch) foundation tests.

Covers: robots.txt matching, sitemap parsing, FTS5 golden layer + BM25,
media persistence, query processing and the algorithmic slop detector.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search.crawlers import RobotsParser, SitemapParser
from search.indexers import turso_indexer
from search.indexers.turso_indexer import TursoGoldenLayer
from search.filters import SlopDetector
from search.query_processor import process_query
from search.dedup import simhash64, hamming_distance, is_near_duplicate, dedupe_texts
from search.indexers.pagerank import pagerank, build_edges
from search.eval import ndcg_at_k, reciprocal_rank


# ============================================================
# robots.txt
# ============================================================

def test_robots_group_selection_and_wildcards():
    parser = RobotsParser()
    rules = parser._parse_robots("""
User-agent: *
Disallow: /private/
Allow: /private/public/
Disallow: /*.pdf$
Crawl-delay: 5

User-agent: RatSearch
Disallow: /rat-only/
Crawl-delay: 2
""")

    assert rules["found"] is True
    assert rules["crawl_delay"] == 2.0

    # RatSearch group wins over *
    assert parser.can_fetch(rules, "https://example.com/rat-only/x") is False
    assert parser.can_fetch(rules, "https://example.com/private/x") is True

    # Longest-match allow override
    rules_star = parser._parse_robots("""
User-agent: *
Disallow: /private/
Allow: /private/public/
Disallow: /*.pdf$
""")
    assert parser.can_fetch(rules_star, "https://example.com/private/secret") is False
    assert parser.can_fetch(rules_star, "https://example.com/private/public/page") is True
    assert parser.can_fetch(rules_star, "https://example.com/file.pdf") is False
    assert parser.can_fetch(rules_star, "https://example.com/file.pdf?dl=1") is True


def test_robots_no_rules_means_allowed():
    parser = RobotsParser()
    rules = parser._parse_robots("User-agent: GPTBot\nDisallow: /")
    assert parser.can_fetch(rules, "https://example.com/anything") is True


# ============================================================
# sitemap parsing
# ============================================================

def test_sitemap_url_parsing():
    parser = SitemapParser()
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc><lastmod>2026-01-01</lastmod><priority>0.8</priority></url>
  <url><loc>https://example.com/b</loc><priority>0.5</priority></url>
</urlset>"""
    entries = parser._parse_sitemap_urls(xml)
    assert [e["url"] for e in entries] == ["https://example.com/a", "https://example.com/b"]
    assert entries[0]["priority"] == 0.8
    assert entries[0]["lastmod"] == "2026-01-01"


# ============================================================
# golden layer: FTS5 + BM25 + media
# ============================================================

def _item(**overrides):
    base = {
        "url": "https://example.com/photosynthesis",
        "title": "Photosynthesis",
        "source_name": "Wikipedia",
        "source_category": "science_research",
        "content_text": "Photosynthesis converts light energy into chemical energy in plants. "
                        "Chlorophyll absorbs blue and red light, reflecting green wavelengths.",
        "content_hash": "abc123",
        "author": "",
        "published_date": "2026-01-01",
        "word_count": 20,
        "quality_score": 0.95,
        "overall_rank": 0.9,
        "authority_score": 0.9,
        "freshness_score": 0.8,
        "media": [{"url": "https://example.com/leaf.jpg", "type": "image", "alt": "leaf"}],
    }
    base.update(overrides)
    return base


@pytest.fixture()
def layer(tmp_path, monkeypatch):
    monkeypatch.setattr(turso_indexer, "DATA_DIR", str(tmp_path))
    layer = TursoGoldenLayer()
    layer.create_tables()
    yield layer
    layer.close()


def test_upsert_and_keyword_search(layer):
    item_id = layer.upsert_knowledge_item(_item())
    assert item_id

    results = layer.keyword_search("photosynthesis", limit=5)
    assert results
    top = results[0]
    assert top["url"] == "https://example.com/photosynthesis"
    assert top["media"][0]["type"] == "image"
    assert 0 < top["similarity_score"] <= 1


def test_fts_trigger_keeps_index_in_sync(layer):
    item = _item()
    layer.upsert_knowledge_item(item)

    item["title"] = "Chlorophyll and light absorption"
    item["content_text"] = "Chlorophyll absorbs blue and red light in the thylakoid membrane."
    layer.upsert_knowledge_item(item)

    assert layer.keyword_search("chlorophyll", limit=5)
    # Old terms were replaced in both title and content — index must follow
    assert not layer.keyword_search("photosynthesis", limit=5)


def test_stats_counts_items(layer):
    layer.upsert_knowledge_item(_item())
    layer.upsert_knowledge_item(_item(url="https://example.com/other", title="Other page"))
    stats = layer.get_stats()
    assert stats["total_items"] == 2
    assert stats["categories"].get("science_research") == 2


# ============================================================
# query processing
# ============================================================

def test_query_processing_spelling_and_category():
    result = process_query("reciepe for bread algorithim")
    assert result["spelling_corrected"] is True
    assert "recipe" in result["corrected"]
    assert "algorithm" in result["corrected"]
    assert result["tokens"]
    assert result["original_words"] >= 3


def test_query_processing_category_inference():
    result = process_query("python database code example")
    assert result["category_inferred"] == "programming_engineering"


def test_fts_query_builder():
    from search.query_processor import QueryProcessor
    qp = QueryProcessor()
    fts = qp._build_fts_query(["photosynthesis", "plants"])
    assert fts == "photosynthesis OR plants"


# ============================================================
# slop detector
# ============================================================

def test_slop_detector_flags_ai_boilerplate():
    detector = SlopDetector()
    ai_text = (
        "As an AI language model, it's important to note that this topic is multifaceted. "
        "Let me break this down for you. Here's a comprehensive overview. "
        "In conclusion, leveraging robust solutions empowers users to delve deeper."
    )
    result = detector.analyze(ai_text, source_name="Unknown Blog",
                              title="AI overview", url="https://unknownblog.example")
    assert result.is_slop is True
    assert result.signals


def test_slop_detector_passes_human_encyclopedia_text():
    detector = SlopDetector()
    human_text = (
        "Photosynthesis is the process by which green plants and some other organisms use "
        "sunlight to synthesize foods from carbon dioxide and water. In 1771, Joseph Priestley "
        "discovered that plants release oxygen. The reaction is 6CO2 + 6H2O -> C6H12O6 + 6O2. "
        "Chlorophyll, the green pigment in leaves, absorbs light most strongly at 430 nm and 662 nm. "
        "See also: [1] https://example.edu/photosynthesis"
    )
    result = detector.analyze(human_text, source_name="Wikipedia",
                              title="Photosynthesis", url="https://en.wikipedia.org/wiki/Photosynthesis")
    assert result.is_slop is False


def test_slop_detector_rejects_social_domains():
    detector = SlopDetector()
    text = (
        "A perfectly ordinary cooking post about pasta with tomatoes and basil. "
        "We cooked it for twenty minutes on a Tuesday evening and it turned out well. "
    ) * 3
    for url in ("https://www.instagram.com/p/abc123", "https://www.facebook.com/groups/123"):
        result = detector.analyze(text, source_name="Social", title="Pasta post", url=url)
        assert result.is_slop is True
        assert any("content farm" in s.lower() or "farm" in s.lower() for s in result.signals)


# ============================================================
# phase 2: simhash, pagerank, slop v3, eval
# ============================================================

def test_simhash_identical_and_distance():
    text = "Photosynthesis converts light energy into chemical energy in plants."
    other = "Quantum mechanics describes nature at the smallest scales."

    h1 = simhash64(text)
    assert h1 == simhash64(text)
    assert is_near_duplicate(h1, simhash64(text), threshold=3)

    distance = hamming_distance(h1, simhash64(other))
    assert 0 < distance <= 64


def test_dedupe_texts_collapses_duplicates():
    texts = [
        "alpha beta gamma delta epsilon zeta eta theta iota kappa",
        "alpha beta gamma delta epsilon zeta eta theta iota kappa",
        "an unrelated passage about ocean currents and salinity gradients",
    ]
    assert dedupe_texts(texts, threshold=3) == [0, 2]


def test_simhash_storage_and_lookup(layer):
    text = "Photosynthesis converts light energy into chemical energy in plants."
    digest = simhash64(text)
    item = _item(content_text=text, title="Photosynthesis")
    item_id = layer.upsert_knowledge_item(item)
    layer.store_simhash(item["url"], digest, item_id)

    assert layer.find_near_duplicate(digest) == item["url"]
    assert layer.find_near_duplicate(simhash64("unrelated topic entirely about volcanoes")) is None


def test_pagerank_hub_scores_higher():
    edges = build_edges([
        ("https://a.example/", "https://b.example/"),
        ("https://a.example/", "https://c.example/"),
        ("https://b.example/", "https://c.example/"),
        ("https://c.example/", "https://a.example/"),
    ])
    ranks = pagerank(edges, iterations=100)
    assert set(ranks) == {"https://a.example/", "https://b.example/", "https://c.example/"}
    assert ranks["https://c.example/"] > ranks["https://b.example/"]


def test_apply_pagerank_updates_authority(layer):
    from search.indexers.pagerank import apply_pagerank

    layer.upsert_knowledge_item(_item(url="https://example.com/a", title="Page A"))
    layer.upsert_knowledge_item(_item(url="https://example.com/b", title="Page B"))
    layer.store_links("https://example.com/a", ["https://example.com/b"])
    layer.store_links("https://example.com/b", ["https://example.com/a"])

    stats = apply_pagerank(layer, iterations=20)
    assert stats["nodes"] >= 2
    assert stats["updated"] >= 2

    session = layer.Session()
    try:
        from sqlalchemy import text as sql_text
        authority = session.execute(
            sql_text("SELECT authority_score FROM knowledge_items WHERE url = 'https://example.com/a'")
        ).scalar()
        assert 0 < authority <= 1
    finally:
        session.close()


def test_slop_v3_flags_repetitive_text_without_provenance():
    detector = SlopDetector()
    text = (
        "In today's fast-paced world, it's important to note that leveraging robust "
        "solutions empowers users to delve deeper into multifaceted topics. "
    ) * 6
    item = {"content_text": text, "author": "", "published_date": "", "external_links": []}
    result = detector.analyze(text, source_name="Unknown", title="Guide",
                              url="https://unknown.example", item=item)
    assert result.compression_ratio < 0.35
    assert result.provenance_score == 0.0
    assert result.is_slop is True


def test_slop_v3_passes_human_doc_with_provenance():
    detector = SlopDetector()
    text = (
        "The Treaty of Westphalia ended the Thirty Years' War in 1648. "
        "Its provisions redrew boundaries across central Europe in ways that still echo. "
        "Negotiators met in Munster and Osnabruck for nearly five years. "
        "Historians disagree about whether it created modern sovereignty or merely reflected it. "
        "The full text is available at https://example.edu/westphalia [1]."
    )
    item = {
        "content_text": text,
        "author": "Jane Scholar",
        "published_date": "2024-03-01",
        "external_links": ["https://a.edu/source", "https://b.org/archive"],
    }
    result = detector.analyze(text, source_name="History Journal", title="Peace of Westphalia",
                              url="https://history.example/peace", item=item)
    assert result.provenance_score >= 0.5
    assert result.is_slop is False


def test_eval_metrics_math():
    ranked = ["https://en.wikipedia.org/wiki/Climate_change", "https://other.example/x"]
    expected = ["en.wikipedia.org/wiki/Climate_change"]

    assert ndcg_at_k(ranked, expected, k=10) == 1.0
    assert reciprocal_rank(ranked, expected) == 1.0
    assert ndcg_at_k(["https://other.example/x"], expected, k=10) == 0.0
    assert reciprocal_rank(["https://other.example/x"], expected) == 0.0


def test_simhash_catches_small_edits_not_unrelated():
    base = (
        "The industrial revolution began in Britain in the late eighteenth century. "
        "Textile mills mechanised spinning and weaving within a few decades. "
        "Coal and iron production expanded rapidly to supply the new machines. "
        "Railways later carried goods and people at unprecedented speed. "
    ) * 2
    edited = base.replace("eighteenth", "eighteen")
    unrelated = (
        "Coral reefs are built by colonies of tiny animals called polyps. "
        "They secrete calcium carbonate skeletons that accumulate over millennia. "
    ) * 4

    assert is_near_duplicate(simhash64(base), simhash64(edited))
    assert not is_near_duplicate(simhash64(base), simhash64(unrelated))


# ============================================================
# phase 2: PPMI thesaurus + query stats
# ============================================================

def test_ppmi_build_associations():
    from search.indexers.thesaurus import build_associations

    texts = [
        "Chlorophyll absorbs light during photosynthesis in green plants.",
        "Photosynthesis converts light energy and chlorophyll captures photons.",
        "Quantum mechanics describes electrons and photons inside atoms.",
    ] * 4

    rows = build_associations(texts)
    pairs = {(term, related) for term, related, _ppmi, _cooc in rows}
    assert ("chlorophyll", "photosynthesis") in pairs or ("photosynthesis", "chlorophyll") in pairs


def test_layer_associations_roundtrip(layer):
    stored = layer.replace_associations([("photosynthesis", "chlorophyll", 2.5, 9)])
    assert stored == 1
    assert layer.get_associations("photosynthesis") == [("chlorophyll", 2.5)]


def test_keyword_search_expands_via_thesaurus(layer):
    layer.upsert_knowledge_item(_item(
        title="Chlorophyll",
        content_text="Chlorophyll absorbs blue and red light in the thylakoid membrane.",
    ))
    layer.replace_associations([("photosynthesis", "chlorophyll", 3.0, 10)])

    results = layer.keyword_search("photosynthesis", limit=5)
    assert results
    assert results[0]["title"] == "Chlorophyll"


def test_layer_query_stats_privacy_safe(layer):
    layer.log_query("Climate Change")
    layer.log_query("climate   change")
    layer.log_query("ocean")

    popular = layer.get_popular_queries(5)
    assert popular[0] == ("climate change", 2)
    assert layer.get_popular_queries(5, prefix="cli") == [("climate change", 2)]


def test_keyword_search_survives_fts_operators(layer):
    layer.upsert_knowledge_item(_item(
        title="Warming",
        content_text="Present-day warming trends affect ocean circulation across the planet.",
    ))
    layer.replace_associations([("climate", "present-day", 3.0, 10)])

    # Hyphenated thesaurus expansion must not be parsed as NOT
    assert layer.keyword_search("climate", limit=5)
    # Hyphen and quote in raw user input must not raise
    assert isinstance(layer.keyword_search('present-day "warming"', limit=5), list)


# ============================================================
# phase 2: Common Crawl ingestion
# ============================================================

def _make_warc(html: bytes, http_headers: bytes) -> bytes:
    import gzip
    warc = (
        b"WARC/1.0\r\n"
        b"WARC-Type: response\r\n"
        b"WARC-Target-URI: https://example.com/\r\n"
        b"Content-Type: application/http; msgtype=response\r\n"
        b"\r\n" + http_headers + html
    )
    return gzip.compress(warc)


def test_cc_parse_cdx_line():
    from search.cc_ingest import parse_cdx_line

    line = ('{"urlkey": "com,example)/", "timestamp": "20260904131850", '
            '"url": "https://www.example.com/", "mime": "text/html", "status": "200", '
            '"digest": "ABC", "length": "954", "offset": "639725720", '
            '"filename": "crawl-data/x.warc.gz"}')
    record = parse_cdx_line(line)
    assert record["url"] == "https://www.example.com/"
    assert record["offset"] == 639725720
    assert record["length"] == 954
    assert parse_cdx_line("not json") is None


def test_cc_warc_response_parsing():
    from search.cc_ingest import parse_warc_response

    html = b"<html><body>Common Crawl test content</body></html>"
    record = _make_warc(html, b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n")
    parsed = parse_warc_response(record)
    assert parsed is not None
    content_type, body = parsed
    assert content_type == "text/html"
    assert "Common Crawl test content" in body


def test_cc_warc_respects_noindex():
    from search.cc_ingest import parse_warc_response

    html = b"<html><body>hidden</body></html>"
    record = _make_warc(
        html,
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nX-Robots-Tag: noindex, nofollow\r\n\r\n",
    )
    assert parse_warc_response(record) is None


def test_cc_dechunk():
    from search.cc_ingest import dechunk

    assert dechunk(b"5\r\nhello\r\n0\r\n\r\n") == b"hello"
    assert dechunk(b"b\r\nhello world\r\n0\r\n\r\n") == b"hello world"


def test_cc_warc_handles_predecoded_gzip_header():
    """CC keeps 'content-encoding: gzip' even when the stored body is plain."""
    from search.cc_ingest import parse_warc_response

    html = b"<html><body>already decoded body</body></html>"
    record = _make_warc(
        html,
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Encoding: gzip\r\n\r\n",
    )
    parsed = parse_warc_response(record)
    assert parsed is not None
    assert "already decoded body" in parsed[1]


def test_cc_warc_handles_actually_gzipped_body():
    import gzip as gz
    from search.cc_ingest import parse_warc_response

    html = b"<html><body>truly compressed body</body></html>"
    record = _make_warc(
        gz.compress(html),
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Encoding: gzip\r\n\r\n",
    )
    parsed = parse_warc_response(record)
    assert parsed is not None
    assert "truly compressed body" in parsed[1]


# ============================================================
# phase 3: backups
# ============================================================

def test_backup_select_prune():
    from search.backup import select_prune

    files = [
        "backup_20260101_000000.jsonl.gz",
        "backup_20260102_000000.jsonl.gz",
        "backup_20260103_000000.jsonl.gz",
        "README.md",
    ]
    assert select_prune(files, 2) == ["backup_20260101_000000.jsonl.gz"]
    assert select_prune(files, 5) == []
    assert select_prune(files, 0) == [
        "backup_20260101_000000.jsonl.gz",
        "backup_20260102_000000.jsonl.gz",
        "backup_20260103_000000.jsonl.gz",
    ]


def test_backup_export_restore_roundtrip(tmp_path, monkeypatch):
    from search.indexers import turso_indexer
    from search.backup import export_jsonl, restore_jsonl

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    monkeypatch.setattr(turso_indexer, "DATA_DIR", str(source_dir))

    layer = TursoGoldenLayer()
    layer.create_tables()
    layer.upsert_knowledge_item(_item())
    layer.upsert_knowledge_item(_item(url="https://example.com/other", title="Other page"))
    layer.close()

    out_path = str(source_dir / "backup_test.jsonl.gz")
    export = export_jsonl(out_path)
    assert export["items"] == 2
    assert export["bytes"] > 0

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    monkeypatch.setattr(turso_indexer, "DATA_DIR", str(target_dir))

    result = restore_jsonl(out_path)
    assert result["restored"] == 2

    restored_layer = TursoGoldenLayer()
    assert restored_layer.get_stats()["total_items"] == 2
    assert restored_layer.keyword_search("photosynthesis", limit=5)
    restored_layer.close()


def test_backup_sqlite_snapshot(tmp_path, monkeypatch):
    from search.indexers import turso_indexer
    from search import backup

    monkeypatch.setattr(turso_indexer, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(backup, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)

    layer = TursoGoldenLayer()
    layer.create_tables()
    layer.upsert_knowledge_item(_item())
    layer.close()

    snapshot = backup.snapshot_sqlite()
    assert snapshot is not None
    assert snapshot["bytes"] > 0
    assert os.path.exists(snapshot["path"])


# ============================================================
# phase 3: Wikipedia dump ingestion
# ============================================================

WIKITEXT_SAMPLE = """
{{Infobox scientist
| name = Ada Lovelace
}}
'''Ada Lovelace''' was an English mathematician and writer.<ref>{{cite book|title=Ada}}</ref>
She is chiefly known for her work on the [[Analytical Engine]] with [[Charles Babbage|Babbage]].
== Career ==
* She wrote the first [[algorithm]] intended for a machine.
* She described loops and [[subroutine|subroutines]].
See [[File:Ada.jpg|thumb|Portrait]] and [[Category:Mathematicians]].
{| class="wikitable"
|-
| a table cell
|}
Text with &amp; entity and a [https://example.org external link].
"""


def test_wikitext_to_text_cleans_markup():
    from search.wiki_dump import wikitext_to_text

    text = wikitext_to_text(WIKITEXT_SAMPLE)
    assert "Ada Lovelace" in text
    assert "Analytical Engine" in text
    assert "Babbage" in text          # piped link keeps the label
    assert "algorithm" in text
    assert "Career" in text           # heading text preserved
    assert "&" in text                # entity decoded
    assert "Infobox" not in text
    assert "cite book" not in text
    assert "File:" not in text
    assert "Category:" not in text
    assert "<ref" not in text
    assert "{|" not in text


def test_extract_wiki_links_skips_namespaces():
    from search.wiki_dump import extract_wiki_links

    links = extract_wiki_links(WIKITEXT_SAMPLE)
    assert "https://en.wikipedia.org/wiki/Analytical_Engine" in links
    assert "https://en.wikipedia.org/wiki/Charles_Babbage" in links
    assert not any("File:" in link or "Category:" in link for link in links)


def test_parse_index_line():
    from search.wiki_dump import parse_index_line

    assert parse_index_line("565:12:Anarchism") == (565, 12, "Anarchism")
    assert parse_index_line("565:361276:George Baker (B: special)") == (
        565, 361276, "George Baker (B: special)")
    assert parse_index_line("garbage") is None


def test_parse_stream_extracts_articles_only():
    from search.wiki_dump import parse_stream

    xml = """
  <page>
    <title>Alpha</title>
    <ns>0</ns>
    <id>1</id>
    <revision><text>Alpha article text</text></revision>
  </page>
  <page>
    <title>Beta</title>
    <ns>0</ns>
    <id>2</id>
    <redirect title="Alpha"/>
    <revision><text>#REDIRECT [[Alpha]]</text></revision>
  </page>
  <page>
    <title>Talk:Alpha</title>
    <ns>1</ns>
    <id>3</id>
    <revision><text>discussion page</text></revision>
  </page>
"""
    pages = parse_stream(xml)
    assert len(pages) == 1
    assert pages[0]["title"] == "Alpha"
    assert pages[0]["page_id"] == "1"
    assert pages[0]["wikitext"] == "Alpha article text"


def test_build_page_shape():
    from search.wiki_dump import build_page

    long_wikitext = (WIKITEXT_SAMPLE + "\n") * 3
    page = build_page("Ada Lovelace", "123", long_wikitext)
    assert page is not None
    assert page["url"] == "https://en.wikipedia.org/wiki/Ada_Lovelace"
    assert page["source_name"] == "Wikipedia"
    assert page["content_hash"]
    assert page["word_count"] > 50
    assert page["internal_links"]
    assert 0 <= page["quality_score"] <= 1
    assert page["metadata"]["source"] == "wikipedia_dump"


def test_wiki_dump_locate_magic():
    from search.wiki_dump import locate_magic

    window = b"\x00" * 100 + b"BZh9payload"
    assert locate_magic(window) == 100
    assert locate_magic(b"no magic here") == -1


# ============================================================
# slop gate: defense in depth (never indexed, no matter what)
# ============================================================

def _slop_item(**overrides):
    base = {
        "url": "https://farm.example.com/ai-guide",
        "title": "Generated by ChatGPT: a comprehensive guide",
        "source_name": "unknown-blog",
        "source_category": "knowledge_foundations",
        "content_text": (
            "As an AI language model, I cannot browse the internet. "
            "Let me break this down for you. Here is a comprehensive summary "
            "of the topic. It is important to note that harnessing the power "
            "of AI empowers users to unlock hidden potential. "
        ) * 20,
        "word_count": 300,
        "quality_score": 0.4,
    }
    base.update(overrides)
    return base


def test_gate_rejects_slop_and_writes_audit(layer):
    from sqlalchemy import text

    assert layer.upsert_knowledge_item(_slop_item()) is None

    with layer.engine.connect() as conn:
        items = conn.execute(text("SELECT COUNT(*) FROM knowledge_items")).scalar()
        rejects = conn.execute(
            text("SELECT url, stage FROM slop_rejects")
        ).fetchall()

    assert items == 0, "slop must never reach knowledge_items"
    assert rejects and rejects[0][0] == "https://farm.example.com/ai-guide"
    assert rejects[0][1] == "index"


def test_gate_accepts_clean_doc_and_persists_verdict(layer):
    from sqlalchemy import text

    item_id = layer.upsert_knowledge_item(_item())
    assert item_id

    with layer.engine.connect() as conn:
        row = conn.execute(
            text("SELECT slop_verdict, slop_score FROM knowledge_items WHERE id = :id"),
            {"id": item_id},
        ).fetchone()
    assert row[0] == "pass"
    assert 0.0 <= row[1] <= 1.0


def test_gate_trusts_carried_verdict_without_reanalysis(layer):
    # A pipeline-rejected item must stay rejected even with clean-looking text
    item = _item(_slop_verdict="rejected", _slop_score=0.9, _slop_signals=["x"])
    assert layer.upsert_knowledge_item(item) is None

    # ...and a carried pass short-circuits too
    ok = _item(url="https://example.com/a", _slop_verdict="pass",
               _slop_score=0.1, _slop_signals=[])
    assert layer.upsert_knowledge_item(ok)


def test_filter_content_carries_verdict_both_ways():
    detector = SlopDetector()
    human, slop = detector.filter_content([_item(), _slop_item()])

    assert human and human[0]["_slop_verdict"] == "pass"
    assert "_slop_score" in human[0]
    assert slop and slop[0]["_slop_verdict"] == "rejected"


def test_purge_removes_pre_gate_slop(layer, monkeypatch):
    from sqlalchemy import text
    from search.purge_slop import purge_slop

    # Simulate a row indexed before the gate existed (direct INSERT)
    with layer.engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO knowledge_items
                    (url, title, source_name, content_text, word_count)
                    VALUES (:url, :title, :src, :content, 300)"""),
            {
                "url": "https://old.example.com/slop",
                "title": "Generated by ChatGPT",
                "src": "unknown",
                "content": _slop_item()["content_text"],
            },
        )
    assert layer.upsert_knowledge_item(_item())

    stats = purge_slop(dry_run=False)
    assert stats["removed"] == 1

    with layer.engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT url FROM knowledge_items ORDER BY id")
        ).fetchall()
        audit = conn.execute(
            text("SELECT url, stage FROM slop_rejects")
        ).fetchall()

    assert [r[0] for r in remaining] == ["https://example.com/photosynthesis"]
    assert audit and audit[0] == ("https://old.example.com/slop", "purge")


def test_purge_dry_run_reports_without_deleting(layer):
    from sqlalchemy import text
    from search.purge_slop import purge_slop

    with layer.engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO knowledge_items
                    (url, title, source_name, content_text, word_count)
                    VALUES ('https://old2.example.com/slop', 'Generated by ChatGPT',
                            'unknown', :content, 300)"""),
            {"content": _slop_item()["content_text"]},
        )

    stats = purge_slop(dry_run=True)
    assert stats["dry_run"] is True and stats["removed"] == 1

    with layer.engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM knowledge_items")).scalar()
    assert count == 1, "dry run must not delete"


# ============================================================
# published dates: extraction, normalization, freshness
# ============================================================

def test_normalize_date_formats():
    from search.crawlers import normalize_date

    assert normalize_date("2024-05-06") == "2024-05-06"
    assert normalize_date("2024-05-06T14:30:00Z") == "2024-05-06"
    assert normalize_date("2024-05-06T14:30:00+02:00") == "2024-05-06"
    assert normalize_date("2024/5/6") == "2024-05-06"
    assert normalize_date("Sun, 20 Sep 2026 01:09:14 GMT") == "2026-09-20"  # HTTP Last-Modified
    assert normalize_date("2024-13-45") == ""
    assert normalize_date("not a date") == ""
    assert normalize_date("") == ""
    assert normalize_date(None) == ""


def test_jsonld_and_priority_resolution():
    from bs4 import BeautifulSoup
    from search.crawlers import jsonld_date_candidates, resolve_date

    html = """
    <html><head>
    <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"Article",
       "datePublished":"2023-11-02T08:00:00Z","dateModified":"2024-01-01"}
    </script>
    <script type="application/ld+json">
      {"@graph":[{"@type":"NewsArticle","datePublished":"2022-07-07"}]}
    </script>
    <meta property="article:published_time" content="2021-01-01">
    <meta property="article:modified_time" content="2025-06-06">
    </head><body><time datetime="2020-03-03"></time></body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = jsonld_date_candidates(soup)

    # JSON-LD wins over meta/time; datePublished beats dateModified
    assert resolve_date(candidates) == "2023-11-02"
    # Full chain: with only meta + time + sitemap, meta published wins
    assert resolve_date(["2021-01-01", "2020-03-03", "2025-06-06", "2019-01-01"]) == "2021-01-01"
    # Sitemap lastmod only reached when everything else fails
    assert resolve_date(["junk", ""]) == ""
    assert resolve_date([]) == ""


def test_freshness_buckets():
    from datetime import datetime, timedelta, timezone
    from search.crawlers import freshness_for_date

    def days_ago(n):
        return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")

    assert freshness_for_date("") == 0.5
    assert freshness_for_date(days_ago(10)) == 1.0
    assert freshness_for_date(days_ago(100)) == 0.8
    assert freshness_for_date(days_ago(300)) == 0.6
    assert freshness_for_date(days_ago(500)) == 0.4
    assert freshness_for_date(days_ago(1000)) == 0.3
    # Year-only fallback still works
    assert freshness_for_date("2020") < 0.6


def test_wiki_dump_revision_timestamp_becomes_published_date():
    from search.wiki_dump import parse_stream, build_page

    xml = """
  <page>
    <title>Thermodynamics</title>
    <ns>0</ns>
    <id>7</id>
    <revision>
      <id>99</id>
      <timestamp>2026-08-15T10:20:30Z</timestamp>
      <text>'''Thermodynamics''' is the branch of physics concerned with heat,
      work and temperature, and their relation to energy, radiation and the
      properties of matter. The second law of thermodynamics states that the
      entropy of an isolated system always increases over time. This article
      covers the four laws, plus classical and statistical mechanics views.</text>
    </revision>
  </page>
"""
    parsed = parse_stream(xml)[0]
    assert parsed["revision_timestamp"] == "2026-08-15T10:20:30Z"

    page = build_page(parsed["title"], parsed["page_id"], parsed["wikitext"],
                      "en", parsed["revision_timestamp"])
    assert page is not None
    assert page["published_date"] == "2026-08-15"


def test_feed_entry_to_record_shapes_feed_item():
    from search.crawlers import feed_entry_to_record

    entry = {
        "link": "https://x.example/dev-post",
        "title": "New <b>Dev</b> Post",
        "summary": "<p>Human written summary with enough words to pass the shape check here.</p>",
        "published": "Tue, 15 Sep 2026 08:00:00 GMT",
        "author": "Ada Engine",
    }
    rec = feed_entry_to_record(entry, "The Verge", "tech_news", 0.93)
    assert rec is not None
    assert rec["published_date"] == "2026-09-15"
    assert rec["source_category"] == "tech_news"
    assert rec["author"] == "Ada Engine"
    assert '<' not in rec["content_text"]
    assert 0 <= rec["overall_rank"] <= 1
    assert rec["content_hash"]

    # empty/audio link items are dropped
    assert feed_entry_to_record({"link": "", "title": "x", "summary": "short"}, "A", "tech_news", 0.9) is None


def test_github_repo_record_builder():
    from search.github_ingest import build_repo_record

    repo = {
        "full_name": "octo/search-engine",
        "html_url": "https://github.com/octo/search-engine",
        "description": "A fast classical IR search engine written by humans.",
        "topics": ["rust", "search"],
        "language": "Rust",
        "stargazers_count": 12345,
        "fork": False,
        "created_at": "2020-05-01T10:00:00Z",
        "owner": {"login": "octo"},
        "license": {"spdx_id": "MIT"},
    }
    rec = build_repo_record(repo, "topic:search-engine")
    assert rec["url"].endswith("/search-engine")
    assert rec["published_date"] == "2020-05-01"
    assert rec["source_category"] == "programming_engineering"
    assert rec["author"] == "octo"
    assert rec["authority_score"] > 0.5
    assert 0 <= rec["overall_rank"] <= 1


def test_sources_load_new_categories():
    import importlib
    from search import sources as ss

    importlib.reload(ss)
    cats = {c.value for c in ss.SourceCategory}
    assert {"tech_news", "health_medical"} <= cats
    tech = ss.get_sources_by_category(ss.SourceCategory.TECH_NEWS)
    assert len(tech) >= 5 and all(s.crawl_type == "rss" for s in tech)
    assert len(ss.ALL_SOURCES) >= 373


def test_backfill_dates_fills_missing_and_preserves_existing(layer, monkeypatch, tmp_path):
    from sqlalchemy import text
    from search import backfill_dates

    with layer.engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO knowledge_items
                    (url, title, content_text, published_date, freshness_score, overall_rank)
                    VALUES ('https://a.example/x', 'X', 'body one', '', 0.5, 0.5)"""),
        )
        conn.execute(
            text("""INSERT INTO knowledge_items
                    (url, title, content_text, published_date, freshness_score, overall_rank)
                    VALUES ('https://a.example/keep', 'K', 'body two', '2010-01-01', 0.3, 0.4)"""),
        )

    monkeypatch.setattr(backfill_dates, "collect_candidates", lambda: {
        "https://a.example/x": "2026-09-01",
    })

    dry = backfill_dates.backfill(dry_run=True)
    assert dry["updated"] == 1

    stats = backfill_dates.backfill(dry_run=False)
    assert stats["updated"] == 1

    with layer.engine.connect() as conn:
        rows = dict((r[0], (r[1], r[2])) for r in conn.execute(
            text("SELECT url, published_date, overall_rank FROM knowledge_items")))
    assert rows["https://a.example/x"][0] == "2026-09-01"
    assert rows["https://a.example/keep"][0] == "2010-01-01", "existing dates must survive"
    # recent date → freshness 1.0 → overall_rank nudged up by 0.15*(1.0-0.5)
    assert abs(rows["https://a.example/x"][1] - 0.575) < 1e-6
