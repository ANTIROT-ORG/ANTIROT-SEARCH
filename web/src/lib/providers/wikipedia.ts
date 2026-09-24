import { fetchJson, stripHtml } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Live Wikipedia search (distinct from the local wiki index): returns pages we
 * haven't crawled yet. Free, human-written, no slop.
 */

interface WikiHit {
  pageid?: number;
  title?: string;
  snippet?: string;
  size?: number;
}

export function mapWikiHit(h: WikiHit): ProviderResult | null {
  if (!h.title) return null;
  const title = stripHtml(h.title).trim();
  if (!title) return null;
  const url = `https://en.wikipedia.org/wiki/${encodeURIComponent(title.replace(/ /g, '_'))}`;
  return {
    id: `wikipedia-${h.pageid || title}`,
    title,
    url,
    snippet: stripHtml(h.snippet || '').replace(/&nbsp;/g, ' ').trim() || 'Wikipedia',
    provider: 'wikipedia',
    result_type: 'web',
    source_name: 'Wikipedia',
    attribution: 'Wikipedia',
  };
}

export async function searchWikipedia(q: string, limit = 6): Promise<ProviderResult[]> {
  const hits = await cached(`prov:wikipedia:${q.toLowerCase()}:${limit}`, 900, async () => {
    const data = await fetchJson<{ query?: { search?: WikiHit[] } }>(
      `https://en.wikipedia.org/w/api.php?action=query&list=search` +
      `&srsearch=${encodeURIComponent(q)}&srlimit=${limit}&format=json`,
      { timeoutMs: 4000 }
    );
    return data?.query?.search || [];
  });
  return (hits || []).map(mapWikiHit).filter(Boolean) as ProviderResult[];
}