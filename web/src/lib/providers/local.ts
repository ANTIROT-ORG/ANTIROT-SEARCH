import { keywordSearch } from '../turso';
import type { SearchResult, SearchFilterOpts } from '../turso';
import { tantivySearch, tantivyEnabled } from '../tantivy';
import type { ProviderResult } from './types';
import { truncate } from './http';

/**
 * Local index adapters — wrap the SQLite/Turso FTS5 layer so it speaks
 * the same language as the federated providers.
 */

/** Tantivy doesn't understand site:/filetype:/date operators — FTS5 does. */
function hasOperators(opts?: SearchFilterOpts): boolean {
  return !!(opts?.ops?.active || opts?.when);
}

function toProviderResult(r: SearchResult): ProviderResult {
  return {
    id: `local-${r.id}`,
    title: r.title,
    url: r.url,
    snippet: truncate(r.content_snippet || '', 300),
    provider: 'local',
    result_type: 'web',
    source_name: r.source_name,
    author: r.author,
    published_date: r.published_date,
    quality_score: r.quality_score,
    score: r.similarity_score,
    media: r.media || [],
    engine: r.search_type === 'tantivy' ? 'tantivy' : 'fts5',
  };
}

export async function localSearch(q: string, limit = 20, category?: string, opts?: SearchFilterOpts): Promise<ProviderResult[]> {
  // Prefer the Tantivy serving index when configured; FTS5 is the fallback.
  // Operator queries bypass Tantivy — only FTS5 applies them.
  if (tantivyEnabled() && !hasOperators(opts)) {
    const viaTantivy = await tantivySearch(q, limit, category);
    if (viaTantivy.length) return viaTantivy.map(toProviderResult);
  }
  const rows = await keywordSearch(q, category, limit, opts);
  return rows.map(toProviderResult);
}

export async function localMedia(q: string, kind: 'image' | 'video', limit = 20, opts?: SearchFilterOpts): Promise<ProviderResult[]> {
  if (limit <= 0) return [];
  const rows = (tantivyEnabled() && !hasOperators(opts))
    ? await tantivySearch(q, 100)
    : await keywordSearch(q, undefined, 100, opts);
  const results: ProviderResult[] = [];

  for (const row of rows) {
    for (const m of row.media || []) {
      const mediaKind = m.type === 'image' ? 'image'
        : (m.type === 'video' || m.type === 'video_embed') ? 'video' : null;
      if (mediaKind !== kind) continue;

      const resultType = mediaKind === 'image' ? 'images' : 'videos';
      results.push({
        id: `local-media-${row.id}-${results.length}`,
        title: row.title,
        url: mediaKind === 'image' ? row.url : (m.url || row.url),
        snippet: truncate(row.content_snippet || '', 200),
        provider: 'local',
        result_type: resultType,
        source_name: row.source_name,
        author: row.author,
        published_date: row.published_date,
        thumbnail: mediaKind === 'image' ? m.url : undefined,
        attribution: row.source_name,
        media: row.media,
        score: row.similarity_score,
      });
      if (results.length >= limit) return results;
    }
  }

  return results;
}

export async function localCode(q: string, limit = 15, opts?: SearchFilterOpts): Promise<ProviderResult[]> {
  if (tantivyEnabled() && !hasOperators(opts)) {
    const hits = await tantivySearch(q, limit, 'programming_engineering');
    if (hits.length) {
      return hits.map(r => ({ ...toProviderResult(r), result_type: 'code' as const, repo: r.source_name }));
    }
  }
  const rows = await keywordSearch(q, 'programming_engineering', limit, opts);
  return rows.map(r => ({ ...toProviderResult(r), result_type: 'code' as const, repo: r.source_name }));
}
