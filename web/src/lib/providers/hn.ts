import { fetchJson } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Hacker News via Algolia — free, fast, human-curated tech (engineers and
 * founders post; no AI-generated content).
 */

interface HnHit {
  objectID?: string;
  title?: string;
  url?: string | null;
  author?: string;
  points?: number;
  num_comments?: number;
  created_at?: string;
}

export function mapHnHit(h: HnHit): ProviderResult | null {
  if (!h.objectID || !h.title) return null;
  const url = h.url || `https://news.ycombinator.com/item?id=${h.objectID}`;
  const meta = [
    h.points ? `${h.points} pts` : '',
    typeof h.num_comments === 'number' ? `${h.num_comments} comments` : '',
    h.author ? `by ${h.author}` : '',
  ].filter(Boolean).join(' · ');
  return {
    id: `hn-${h.objectID}`,
    title: h.title,
    url,
    snippet: meta || 'Hacker News',
    provider: 'hn',
    result_type: 'news',
    author: h.author,
    published_date: h.created_at || undefined,
    attribution: 'Hacker News',
  };
}

export async function searchHn(q: string, limit = 8): Promise<ProviderResult[]> {
  const hits = await cached(`prov:hn:${q.toLowerCase()}:${limit}`, 180, async () => {
    const data = await fetchJson<{ hits?: HnHit[] }>(
      `https://hn.algolia.com/api/v1/search?query=${encodeURIComponent(q)}` +
      `&hitsPerPage=${limit}&tags=story`,
      { timeoutMs: 4000 }
    );
    return (data?.hits || []).slice(0, limit);
  });
  return (hits || []).map(mapHnHit).filter(Boolean) as ProviderResult[];
}