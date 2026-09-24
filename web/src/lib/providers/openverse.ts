import { fetchJson, stripHtml, truncate } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Openverse — CC-licensed image search (keyless, anonymously rate-limited).
 */

interface OpenverseResponse {
  results?: {
    id: string;
    title?: string;
    url?: string;
    thumbnail?: string;
    creator?: string;
    license?: string;
    license_version?: string;
    foreign_landing_url?: string;
    source?: string;
    description?: string;
  }[];
}

export async function searchOpenverse(q: string, limit = 24, page = 1): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const url = 'https://api.openverse.org/v1/images/' +
    `?q=${encodeURIComponent(q)}&page=${page}&page_size=${limit}`;

  const data = await cached(`openverse:${q.toLowerCase()}:${limit}:${page}`, 1800, () =>
    fetchJson<OpenverseResponse>(url, { timeoutMs: 6000 })
  );

  return (data?.results || [])
    .filter(r => r.thumbnail || r.url)
    .map(r => ({
      id: `openverse-${r.id}`,
      title: stripHtml(r.title || 'Untitled image'),
      url: r.foreign_landing_url || r.url || '',
      snippet: truncate(stripHtml(r.description || ''), 200),
      provider: 'openverse',
      result_type: 'images' as const,
      source_name: r.source ? `Openverse · ${r.source}` : 'Openverse',
      thumbnail: r.thumbnail || r.url,
      license: r.license ? `${r.license}${r.license_version ? ' ' + r.license_version : ''}` : '',
      attribution: stripHtml(r.creator || ''),
    }));
}
