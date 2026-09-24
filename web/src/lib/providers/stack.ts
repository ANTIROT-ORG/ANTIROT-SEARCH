import { fetchJson } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Stack Exchange (Stack Overflow) — expert, human-written Q&A. The API is
 * free, gzip-encoded by default (Node fetch auto-decompresses it).
 */

interface SeItem {
  question_id?: number;
  title?: string;
  link?: string;
  score?: number;
  is_answered?: boolean;
  answer_count?: number;
  tags?: string[];
  creation_date?: number;
  owner?: { display_name?: string };
}

export function mapSeItem(item: SeItem, site = 'stackoverflow'): ProviderResult | null {
  if (!item.title) return null;
  const id = item.question_id;
  const url = item.link || `https://${site}.com/questions/${id}`;
  const meta: string[] = [];
  if (typeof item.score === 'number' && item.score !== 0) meta.push(`${item.score} score`);
  if (typeof item.answer_count === 'number') meta.push(`${item.answer_count} answers`);
  if (item.is_answered) meta.push('accepted');
  const tags = (item.tags || []).slice(0, 5).join(', ');
  return {
    id: `stack-${id || Math.random()}`,
    title: item.title,
    url,
    snippet: `${meta.join(' · ')}${tags ? ` · ${tags}` : ''}`.trim() || 'Stack Overflow',
    provider: 'stack',
    result_type: 'code',
    author: item.owner?.display_name,
    published_date: item.creation_date
      ? new Date(item.creation_date * 1000).toISOString()
      : undefined,
    attribution: 'Stack Exchange',
  };
}

export async function searchStack(q: string, site = 'stackoverflow', limit = 8): Promise<ProviderResult[]> {
  const hits = await cached(`prov:stack:${site}:${q.toLowerCase()}:${limit}`, 600, async () => {
    const data = await fetchJson<{ items?: SeItem[] }>(
      `https://api.stackexchange.com/2.3/search/advanced` +
      `?order=desc&sort=relevance&site=${encodeURIComponent(site)}` +
      `&q=${encodeURIComponent(q)}&pagesize=${limit}`,
      { timeoutMs: 4500 }
    );
    return data?.items || [];
  });
  return (hits || []).map(i => mapSeItem(i, site)).filter(Boolean) as ProviderResult[];
}