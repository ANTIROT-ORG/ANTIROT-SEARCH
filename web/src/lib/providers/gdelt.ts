import { fetchJson, stripHtml } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';
import { domainOf } from './slop';

/**
 * GDELT DOC 2.0 — keyless global news index.
 *
 * Clean-news policy: results are restricted to a whitelist of reputable
 * outlets (wires, public broadcasters, quality press, journals, .gov/.edu/.int).
 * Facebook/Instagram/TikTok and content farms can never surface.
 *
 * GDELT asks for max 1 request / 5s and rate-limits shared IPs with a
 * plain-text notice — fetchJson treats that as null and we degrade quietly.
 */

const NEWS_ALLOWLIST = new Set([
  // Wire services
  'reuters.com', 'apnews.com', 'afp.com', 'dpa.com', 'efe.com',
  // Public broadcasters
  'bbc.com', 'bbc.co.uk', 'npr.org', 'pbs.org', 'cbc.ca', 'abc.net.au',
  'dw.com', 'france24.com', 'aljazeera.com', 'cna.asia', 'nhk.or.jp',
  // Quality press
  'theguardian.com', 'nytimes.com', 'washingtonpost.com', 'wsj.com',
  'ft.com', 'economist.com', 'bloomberg.com', 'cnbc.com', 'forbes.com',
  'politico.com', 'axios.com', 'theatlantic.com', 'newyorker.com',
  'wired.com', 'arstechnica.com', 'technologyreview.com',
  'thehindu.com', 'indianexpress.com', 'scmp.com', 'japantimes.co.jp',
  'lemonde.fr', 'spiegel.de', 'elpais.com', 'corriere.it',
  // Science / research press
  'nature.com', 'science.org', 'scientificamerican.com', 'quantamagazine.org',
  'newscientist.com', 'phys.org', 'statnews.com',
  // Institutional
  'who.int', 'un.org', 'worldbank.org', 'imf.org', 'oecd.org',
  'nasa.gov', 'nih.gov', 'cdc.gov', 'europa.eu',
]);

const NEWS_ALLOWED_SUFFIXES = ['.gov', '.edu', '.int', '.ac.uk'];

export function isReputableNewsDomain(url: string): boolean {
  const domain = domainOf(url);
  if (!domain) return false;
  if (NEWS_ALLOWLIST.has(domain)) return true;
  for (const allowed of NEWS_ALLOWLIST) {
    if (domain.endsWith('.' + allowed)) return true;
  }
  return NEWS_ALLOWED_SUFFIXES.some(suffix => domain.endsWith(suffix));
}

interface GdeltResponse {
  articles?: {
    url?: string;
    title?: string;
    seendate?: string;
    domain?: string;
    language?: string;
    sourcecountry?: string;
  }[];
}

// GDELT rate-limits shared IPs with plain-text notices. After two
// consecutive failures we back off for 15 minutes so searches stay fast.
let consecutiveFailures = 0;
let downUntil = 0;

function parseGdeltDate(value: string | undefined): string {
  if (!value) return '';
  // e.g. 20260919T213000Z
  const m = value.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
  if (!m) return '';
  return `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`;
}

export async function searchGdelt(q: string, limit = 30, page = 1): Promise<ProviderResult[]> {
  if (!q.trim() || page > 1 || Date.now() < downUntil) return [];

  const url = 'https://api.gdeltproject.org/api/v2/doc/doc' +
    `?query=${encodeURIComponent(q)}&mode=artlist&maxrecords=${Math.min(limit, 75)}` +
    `&format=json&sort=datedesc`;

  const data = await cached(`gdelt:${q.toLowerCase()}:${limit}`, 900, () =>
    fetchJson<GdeltResponse>(url, { timeoutMs: 4000 })
  );

  const articles = Array.isArray(data?.articles) ? data!.articles! : [];
  if (!articles.length) {
    consecutiveFailures++;
    if (consecutiveFailures >= 2) downUntil = Date.now() + 15 * 60_000;
    return [];
  }
  consecutiveFailures = 0;
  return articles
    .filter(a => a.url && a.title && isReputableNewsDomain(a.url))
    .map((a, i) => ({
      id: `gdelt-${a.domain || i}-${i}`,
      title: stripHtml(a.title || ''),
      url: a.url || '',
      snippet: '',
      provider: 'gdelt',
      result_type: 'news' as const,
      source_name: a.domain || domainOf(a.url || ''),
      published_date: parseGdeltDate(a.seendate),
    }));
}
