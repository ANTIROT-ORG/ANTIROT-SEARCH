import { fetchJson, stripHtml, truncate } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Internet Archive — keyless search across movies, images and texts.
 * Thumbnails come from the archive.org services/img endpoint.
 */

interface ArchiveResponse {
  response?: {
    docs?: {
      identifier: string;
      title?: string | string[];
      description?: string | string[];
      year?: string | number;
      creator?: string | string[];
      mediatype?: string;
    }[];
  };
}

function firstValue(value: string | string[] | number | undefined): string {
  if (Array.isArray(value)) return String(value[0] ?? '');
  return value == null ? '' : String(value);
}

async function searchArchive(
  q: string,
  mediatype: 'movies' | 'image',
  limit: number,
  page: number
): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const query = `${q} AND mediatype:(${mediatype})`;
  const url = 'https://archive.org/advancedsearch.php' +
    `?q=${encodeURIComponent(query)}` +
    `&fl[]=identifier&fl[]=title&fl[]=description&fl[]=year&fl[]=creator` +
    `&rows=${limit}&page=${page}&output=json`;

  const data = await cached(`archive:${mediatype}:${q.toLowerCase()}:${limit}:${page}`, 1800, () =>
    fetchJson<ArchiveResponse>(url, { timeoutMs: 7000 })
  );

  const docs = data?.response?.docs || [];
  return docs
    .filter(d => d.identifier)
    .map(d => ({
      id: `archive-${d.identifier}`,
      title: firstValue(d.title) || d.identifier,
      url: `https://archive.org/details/${encodeURIComponent(d.identifier)}`,
      snippet: truncate(stripHtml(firstValue(d.description)), 220),
      provider: 'archive',
      result_type: mediatype === 'movies' ? ('videos' as const) : ('images' as const),
      source_name: 'Internet Archive',
      author: firstValue(d.creator),
      published_date: firstValue(d.year),
      thumbnail: `https://archive.org/services/img/${encodeURIComponent(d.identifier)}`,
    }));
}

export function searchArchiveVideos(q: string, limit = 20, page = 1): Promise<ProviderResult[]> {
  return searchArchive(q, 'movies', limit, page);
}

export function searchArchiveImages(q: string, limit = 20, page = 1): Promise<ProviderResult[]> {
  return searchArchive(q, 'image', limit, page);
}
