import { fetchJson, stripHtml, truncate, env } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * OpenAlex + Crossref — keyless research paper search.
 */

function abstractFromInverted(index: Record<string, number[]> | undefined): string {
  if (!index) return '';
  const words: { word: string; pos: number }[] = [];
  for (const [word, positions] of Object.entries(index)) {
    for (const pos of positions || []) words.push({ word, pos });
  }
  words.sort((a, b) => a.pos - b.pos);
  return words.map(w => w.word).join(' ');
}

interface OpenAlexResponse {
  results?: {
    id: string;
    title?: string;
    display_name?: string;
    publication_year?: number;
    doi?: string;
    abstract_inverted_index?: Record<string, number[]>;
    authorships?: { author?: { display_name?: string } }[];
    primary_location?: {
      landing_page_url?: string;
      source?: { display_name?: string };
    };
    open_access?: { oa_status?: string; oa_url?: string };
  }[];
}

export async function searchOpenAlex(q: string, limit = 10, page = 1): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const mailto = env('OPENALEX_MAILTO') || 'antirot@example.com';
  const url = 'https://api.openalex.org/works' +
    `?search=${encodeURIComponent(q)}&per-page=${limit}&page=${page}` +
    `&mailto=${encodeURIComponent(mailto)}`;

  const data = await cached(`openalex:${q.toLowerCase()}:${limit}:${page}`, 1800, () =>
    fetchJson<OpenAlexResponse>(url, { timeoutMs: 6000 })
  );

  return (data?.results || [])
    .filter(w => w.title || w.display_name)
    .map(w => ({
      id: `openalex-${w.id}`,
      title: stripHtml(w.title || w.display_name || ''),
      url: w.primary_location?.landing_page_url || w.doi || w.id,
      snippet: truncate(stripHtml(abstractFromInverted(w.abstract_inverted_index)), 300),
      provider: 'openalex',
      result_type: 'research' as const,
      source_name: w.primary_location?.source?.display_name || 'OpenAlex',
      author: w.authorships?.[0]?.author?.display_name || '',
      published_date: w.publication_year ? `${w.publication_year}-01-01` : '',
      license: w.open_access?.oa_status || '',
    }));
}

interface CrossrefResponse {
  message?: {
    items?: {
      DOI?: string;
      title?: string[];
      abstract?: string;
      author?: { given?: string; family?: string }[];
      issued?: { 'date-parts'?: number[][] };
      URL?: string;
      'container-title'?: string[];
    }[];
  };
}

export async function searchCrossref(q: string, limit = 10, page = 1): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const offset = (page - 1) * limit;
  const url = 'https://api.crossref.org/works' +
    `?query=${encodeURIComponent(q)}&rows=${limit}&offset=${offset}`;

  const data = await cached(`crossref:${q.toLowerCase()}:${limit}:${page}`, 1800, () =>
    fetchJson<CrossrefResponse>(url, { timeoutMs: 6000 })
  );

  return (data?.message?.items || [])
    .filter(item => item.DOI)
    .map(item => {
      const parts = item.issued?.['date-parts']?.[0] || [];
      const date = parts.length ? parts.map((p, i) => (i === 0 ? p : String(p).padStart(2, '0'))).join('-') : '';
      const firstAuthor = item.author?.[0];
      return {
        id: `crossref-${item.DOI}`,
        title: stripHtml(item.title?.[0] || item.DOI || ''),
        url: item.URL || `https://doi.org/${item.DOI}`,
        snippet: truncate(stripHtml(item.abstract || '').replace(/^Abstract\s*/i, ''), 300),
        provider: 'crossref',
        result_type: 'research' as const,
        source_name: item['container-title']?.[0] || 'Crossref',
        author: firstAuthor ? [firstAuthor.given, firstAuthor.family].filter(Boolean).join(' ') : '',
        published_date: date,
      };
    });
}
