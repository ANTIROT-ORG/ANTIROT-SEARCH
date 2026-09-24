import type { SearchResult } from './turso';

/**
 * Optional Tantivy serving index (rust_search/).
 * Enabled by setting TANTIVY_URL (e.g. http://127.0.0.1:8090).
 * Falls back silently to SQLite FTS5 when unset or unreachable.
 */

function baseUrl(): string {
  const meta = (import.meta as any).env || {};
  const fromMeta = meta.TANTIVY_URL || '';
  if (fromMeta) return String(fromMeta);
  if (typeof process !== 'undefined') return process.env?.TANTIVY_URL || '';
  return '';
}

export function tantivyEnabled(): boolean {
  return !!baseUrl();
}

export async function tantivySearch(
  query: string,
  limit = 20,
  category?: string
): Promise<SearchResult[]> {
  const base = baseUrl();
  if (!base || !query.trim()) return [];

  try {
    const url = `${base.replace(/\/$/, '')}/search` +
      `?q=${encodeURIComponent(query)}&limit=${Math.min(limit, 100)}` +
      (category ? `&category=${encodeURIComponent(category)}` : '');

    const res = await fetch(url, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) return [];

    const data = await res.json() as { results?: any[] };
    return (data.results || []).map((hit: any): SearchResult => ({
      id: Number(hit.id) || 0,
      url: String(hit.url || ''),
      title: String(hit.title || ''),
      source_name: String(hit.source_name || ''),
      source_category: String(hit.source_category || ''),
      content_snippet: String(hit.content_snippet || ''),
      author: String(hit.author || ''),
      published_date: String(hit.published_date || ''),
      word_count: Number(hit.word_count) || 0,
      quality_score: Number(hit.quality_score) || 0,
      overall_rank: 0,
      authority_score: Number(hit.authority_score) || 0,
      freshness_score: Number(hit.freshness_score) || 0,
      similarity_score: Number(hit.similarity_score) || 0,
      search_type: 'tantivy',
      media: Array.isArray(hit.media) ? hit.media : [],
    }));
  } catch {
    return [];
  }
}
