import { fetchText } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Common Crawl CDX index — a queryable index of the open web, no peta-byte
 * download (as desired). Best when paired with the `site:` operator: it finds
 * pages under a domain we have never crawled ourselves. Lookup-oriented and
 * rate-limited, so it is strictly one source among several.
 */

interface CollInfo { id: string; 'cdx-api'?: string }

const COLL_KEY = 'cc:collinfo';

async function latestCollection(): Promise<{ label: string; cdxApi: string } | null> {
  const colls = await cached<CollInfo[]>(COLL_KEY, 86400, async () => {
    const data = await fetchText(
      'https://index.commoncrawl.org/collinfo.json',
      { timeoutMs: 6000 }
    );
    try {
      return JSON.parse(data || '[]') as CollInfo[];
    } catch {
      return [];
    }
  });
  if (!colls || !colls.length) return null;
  const latest = colls.reduce((a, b) => (a.id >= b.id ? a : b));
  if (!latest['cdx-api']) return null;
  return { label: latest.id, cdxApi: latest['cdx-api'] };
}

/** Human-ish title from a URL path (last two meaningful segments). */
export function titleFromCdxUrl(url: string): string {
  try {
    const u = new URL(url);
    const parts = u.pathname.split('/').filter(p => p && p !== 'index.html');
    const tail = parts.slice(-2).map(p => decodeURIComponent(p).replace(/\.(html?|php)$/i, ''));
    const joined = tail.length ? tail.join(' · ') : u.hostname.replace(/^www\./, '');
    return (joined.replace(/[-_]+/g, ' ').trim() || url) as string;
  } catch {
    return url;
  }
}

interface CdxRow { url?: string; timestamp?: string }

export function mapCdxRow(row: CdxRow, label: string): ProviderResult | null {
  const url = row.url;
  if (!url) return null;
  const ts = row.timestamp || '';
  const published = /^(\d{4})(\d{2})(\d{2})/.test(ts)
    ? `${ts.slice(0, 4)}-${ts.slice(4, 6)}-${ts.slice(6, 8)}`
    : undefined;
  const captured = ts ? ` · captured ${ts.slice(0, 4)}-${ts.slice(4, 6)}-${ts.slice(6, 8)}` : '';
  return {
    id: `cc-${url}-${ts}`,
    title: titleFromCdxUrl(url),
    url,
    snippet: `Indexed in Common Crawl ${label}${captured}`,
    provider: 'ccindex',
    result_type: 'web',
    published_date: published,
    source_name: 'Common Crawl',
    attribution: 'Common Crawl',
  };
}

export async function searchCcIndex(
  query: string,
  site: string | undefined,
  limit = 12
): Promise<ProviderResult[]> {
  if (!site) return [];

  const coll = await latestCollection();
  if (!coll) return [];

  // Use the first distinctive query term as a URL-path filter (AND-only API)
  const term = (query || '').replace(/site:\S+/i, '').split(/\s+/)
    .find(t => t.length >= 3);
  const filter = term
    ? `&filter=~url:.*${term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}.*`
    : '';

  const text = await fetchText(
    `${coll.cdxApi}?url=${encodeURIComponent(site)}/*` +
    `&output=json&limit=${limit * 2}&collapse=urlkey&filter=~status:200${filter}`,
    { timeoutMs: 6000 }
  );
  if (!text) return [];

  const rows: CdxRow[] = [];
  for (const line of text.split('\n')) {
    const l = line.trim();
    if (!l) continue;
    try {
      rows.push(JSON.parse(l) as CdxRow);
    } catch { /* skip malformed NDJSON line */ }
    if (rows.length >= limit * 2) break;
  }
  return rows.slice(0, limit).map(r => mapCdxRow(r, coll.label)).filter(Boolean) as ProviderResult[];
}