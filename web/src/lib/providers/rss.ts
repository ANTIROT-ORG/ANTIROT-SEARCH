import { fetchText, stripHtml, truncate } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Curated RSS news provider — keyless and reliable.
 *
 * This is the clean-news core: only outlets on the hardcoded list are
 * fetched, so social platforms and content farms can never appear.
 * GDELT adds breadth on top when it is reachable.
 */

const FEEDS: { name: string; url: string }[] = [
  { name: 'BBC World', url: 'https://feeds.bbci.co.uk/news/world/rss.xml' },
  { name: 'NPR News', url: 'https://feeds.npr.org/1001/rss.xml' },
  { name: 'Al Jazeera', url: 'https://www.aljazeera.com/xml/rss/all.xml' },
  { name: 'DW', url: 'https://rss.dw.com/rdf/rss-en-all' },
  { name: 'The Guardian', url: 'https://www.theguardian.com/world/rss' },
  { name: 'PBS NewsHour', url: 'https://www.pbs.org/newshour/feeds/rss/headlines' },
  { name: 'CBC', url: 'https://www.cbc.ca/webfeed/rss/rss-topstories' },
  { name: 'ABC Australia', url: 'https://www.abc.net.au/news/feed/51120/rss.xml' },
  { name: 'France 24', url: 'https://www.france24.com/en/rss' },
  { name: 'New York Times World', url: 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml' },
  { name: 'Washington Post World', url: 'https://feeds.washingtonpost.com/rss/world' },
  { name: 'Ars Technica', url: 'https://feeds.arstechnica.com/arstechnica/index' },
  { name: 'MIT Tech Review', url: 'https://www.technologyreview.com/feed/' },
  { name: 'Nature', url: 'https://www.nature.com/nature.rss' },
  { name: 'Science News', url: 'https://www.science.org/rss/news_current.xml' },
  { name: 'Quanta Magazine', url: 'https://www.quantamagazine.org/feed/' },
];

interface FeedItem {
  title: string;
  url: string;
  snippet: string;
  published_date: string;
  feedName: string;
}

function extractTag(block: string, name: string): string {
  const m = block.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)<\\/${name}>`, 'i'));
  if (!m) return '';
  return m[1].replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').trim();
}

function parseDate(value: string): string {
  if (!value) return '';
  const ts = Date.parse(value);
  return isNaN(ts) ? '' : new Date(ts).toISOString();
}

export function parseFeed(xml: string, feedName: string): FeedItem[] {
  const items: FeedItem[] = [];
  const blocks = xml.match(/<item[\s>][\s\S]*?<\/item>|<entry[\s>][\s\S]*?<\/entry>/gi) || [];

  for (const block of blocks.slice(0, 40)) {
    const title = extractTag(block, 'title');
    let link = extractTag(block, 'link');
    if (!link) {
      const href = block.match(/<link[^>]*href="([^"]+)"/i);
      link = href?.[1] || '';
    }
    const pub = extractTag(block, 'pubDate') || extractTag(block, 'updated') ||
                extractTag(block, 'published') || extractTag(block, 'dc:date');
    const desc = extractTag(block, 'description') || extractTag(block, 'summary') ||
                 extractTag(block, 'content');

    if (title && link) {
      items.push({
        title: stripHtml(title),
        url: link.trim(),
        snippet: truncate(stripHtml(desc), 220),
        published_date: parseDate(pub),
        feedName,
      });
    }
  }

  return items;
}

async function fetchFeed(feed: { name: string; url: string }): Promise<FeedItem[]> {
  const xml = await cached(`rss:${feed.url}`, 900, () =>
    fetchText(feed.url, { timeoutMs: 5000 })
  );
  if (!xml) return [];
  try {
    return parseFeed(xml, feed.name);
  } catch {
    return [];
  }
}

export async function searchRss(q: string, limit = 25, page = 1): Promise<ProviderResult[]> {
  if (page > 1) return [];

  const settled = await Promise.all(FEEDS.map(f => fetchFeed(f)));
  const all = settled.flat();

  const tokens = q.toLowerCase().split(/\s+/).filter(t => t.length > 1);

  const scored = all.map(item => {
    const text = `${item.title} ${item.snippet}`.toLowerCase();
    const hits = tokens.filter(t => text.includes(t)).length;
    const match = tokens.length ? hits / tokens.length : 0;
    return { item, match };
  });

  const matched = scored.filter(s => s.match > 0);
  const pool = matched.length ? matched : scored;

  pool.sort((a, b) => {
    const dateA = Date.parse(a.item.published_date) || 0;
    const dateB = Date.parse(b.item.published_date) || 0;
    if (b.match !== a.match) return b.match - a.match;
    return dateB - dateA;
  });

  return pool.slice(0, limit).map(({ item }, i) => ({
    id: `rss-${item.feedName}-${i}`,
    title: item.title,
    url: item.url,
    snippet: item.snippet,
    provider: 'rss',
    result_type: 'news' as const,
    source_name: item.feedName,
    published_date: item.published_date,
  }));
}
