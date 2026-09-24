import { fetchJson, stripHtml, truncate } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Wikimedia Commons image search — keyless, licensed media with attribution.
 */

interface CommonsResponse {
  query?: {
    pages?: Record<string, {
      pageid: number;
      title?: string;
      imageinfo?: {
        url?: string;
        descriptionurl?: string;
        thumburl?: string;
        extmetadata?: Record<string, { value?: string }>;
      }[];
    }>;
  };
}

export async function searchCommons(q: string, limit = 24, page = 1): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const offset = (page - 1) * limit;
  const url = 'https://commons.wikimedia.org/w/api.php' +
    `?action=query&format=json&origin=*&generator=search&gsrnamespace=6` +
    `&gsrsearch=${encodeURIComponent(q)}&gsrlimit=${limit}&gsroffset=${offset}` +
    `&prop=imageinfo&iiprop=url|size|extmetadata&iiurlwidth=400`;

  const data = await cached(`commons:${q.toLowerCase()}:${limit}:${page}`, 900, () =>
    fetchJson<CommonsResponse>(url, { timeoutMs: 6000 })
  );

  const pages = Object.values(data?.query?.pages || {});
  const results: ProviderResult[] = [];

  for (const page of pages) {
    const info = page.imageinfo?.[0];
    if (!info?.url) continue;

    const meta = info.extmetadata || {};
    results.push({
      id: `commons-${page.pageid}`,
      title: stripHtml((page.title || '').replace(/^File:/, '')),
      url: info.descriptionurl || info.url,
      snippet: truncate(stripHtml(meta.ImageDescription?.value || ''), 200),
      provider: 'commons',
      result_type: 'images',
      source_name: 'Wikimedia Commons',
      thumbnail: info.thumburl || info.url,
      license: stripHtml(meta.LicenseShortName?.value || ''),
      attribution: stripHtml(meta.Artist?.value || ''),
      published_date: stripHtml(meta.DateTimeOriginal?.value || ''),
    });
  }

  return results;
}
