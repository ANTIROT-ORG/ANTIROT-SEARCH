import type { APIRoute } from 'astro';
import { ensureTables, processQuery, logQuery } from '../../lib/turso';
import { getCached, setCache } from '../../lib/redis';
import { federatedSearch, isVertical } from '../../lib/providers/federation';
import { relatedSearches } from '../../lib/providers/related';
import { parseQueryOps, opsChips, VALID_WHEN } from '../../lib/queryops';
import type { OpsChip } from '../../lib/queryops';
import { didYouMean, exactCorrection } from '../../lib/spell';
import { peopleAlsoAsk } from '../../lib/providers/paa';
import type { PaaItem } from '../../lib/providers/paa';
import { isDictionaryQuery, fetchDictionaryBudgeted } from '../../lib/dictionary';
import type { DictionaryEntry } from '../../lib/dictionary';

const MAX_QUERY_LEN = 200;
const MAX_LOCAL_RESULTS = 100;
const RATE_LIMIT_MAP = new Map<string, { count: number; resetAt: number }>();
const RATE_LIMIT = 30;

// Stale-while-revalidate: serve cached hits instantly; once an entry ages
// past SWR_SECONDS a single background refresh recomputes it (providers get
// another chance to land) so the next visitor gets the full result set.
const SWR_SECONDS = 45;
const CACHE_TTL_S = 900;
const REFRESH_HEADER = 'x-rat-refresh';
const refreshing = new Set<string>();

interface AnswerCard {
  text: string;
  url: string;
  title: string;
  source_name: string;
}

function checkRateLimit(ip: string): boolean {
  const now = Date.now();
  const entry = RATE_LIMIT_MAP.get(ip);
  if (!entry || now > entry.resetAt) {
    RATE_LIMIT_MAP.set(ip, { count: 1, resetAt: now + 60_000 });
    return true;
  }
  if (entry.count >= RATE_LIMIT) return false;
  entry.count++;
  return true;
}

function buildInterpretation(processed: ReturnType<typeof processQuery>) {
  return {
    normalized: processed.normalized,
    corrected: processed.spellingCorrected ? processed.corrected : null,
    tokens: processed.tokens,
    expanded: processed.expandedTokens.filter(t => !processed.tokens.includes(t)),
    category: processed.categoryInferred,
  };
}

/** Featured answer: prefers a title-matched result, else a strongly covered snippet. */
export function buildAnswer(q: string, results: { title: string; url: string; snippet?: string; source_name?: string }[]): AnswerCard | null {
  const qTokens = (q.toLowerCase().match(/[a-z0-9]{3,}/g) || []);
  if (!qTokens.length) return null;

  const usable = results.filter(r => r.snippet && r.snippet.length >= 60);
  if (!usable.length) return null;

  // A result that is ON the topic (title hit) outranks a mere snippet mention
  const titleMatch = usable.find(r => {
    const title = r.title.toLowerCase();
    return qTokens.some(t => title.includes(t));
  });

  const candidates = titleMatch ? [titleMatch] : usable.slice(0, 3);
  for (const top of candidates) {
    const haystack = top.snippet!.toLowerCase();
    const covered = qTokens.filter(t => haystack.includes(t)).length;
    if (covered / qTokens.length < 0.6) continue;

    // Without a title match, demand the topic appear early in the passage
    if (!titleMatch) {
      const firstSentence = top.snippet!.split(/(?<=[.!?])\s+/)[0].toLowerCase();
      if (!qTokens.some(t => firstSentence.includes(t))) continue;
    }

    return {
      text: top.snippet!,
      url: top.url,
      title: top.title,
      source_name: top.source_name || '',
    };
  }
  return null;
}

export const GET: APIRoute = async ({ url, clientAddress, request }) => {
  const raw = url.searchParams.get('q') || '';
  const verticalParam = url.searchParams.get('vertical') || 'all';
  const vertical = isVertical(verticalParam) ? verticalParam : 'all';
  const category = url.searchParams.get('category') || undefined;
  const whenParam = (url.searchParams.get('when') || 'any').toLowerCase();
  const when = VALID_WHEN.has(whenParam) && whenParam !== 'any' ? whenParam : '';
  const limit = Math.min(parseInt(url.searchParams.get('limit') || '20'), 50);
  const page = Math.max(parseInt(url.searchParams.get('page') || '1'), 1);
  // Fast phase: top-5 results + knowledge only; the full response streams in
  const fast = url.searchParams.get('fast') === '1' && vertical === 'all' && page === 1;

  if (!raw.trim() || raw.length > MAX_QUERY_LEN) {
    return new Response(JSON.stringify({ error: 'Invalid query' }), {
      status: 400, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
    });
  }

  const ip = clientAddress || 'unknown';
  if (!checkRateLimit(ip)) {
    return new Response(JSON.stringify({ error: 'Rate limited' }), {
      status: 429, headers: { 'Content-Type': 'application/json',
        'Cache-Control': 'no-store', 'Retry-After': '60' },
    });
  }

  // Operators are parsed BEFORE normalization so quotes/colons survive
  const ops = parseQueryOps(raw);
  const processed = processQuery(ops.cleaned || raw);
  const q = processed.corrected;
  const chips: OpsChip[] = opsChips(ops);

  // Aggregate query stats (no IP/user identifiers stored) — best-effort
  const isRefresh = request.headers.get(REFRESH_HEADER) === '1';
  if (!isRefresh) void logQuery(q);

  const maxPages = vertical === 'code' ? 5 : Math.max(1, Math.floor(MAX_LOCAL_RESULTS / limit));
  if (page > maxPages) {
    return new Response(JSON.stringify({
      query: q,
      original_query: raw,
      vertical,
      results: [],
      total_results: 0,
      search_type: 'beyond-depth',
      page,
      pages_available: maxPages,
      operators: chips,
      when: when || 'any',
      phase: 'full',
      interpretation: buildInterpretation(processed),
    }), {
      headers: { 'Content-Type': 'application/json',
        'Cache-Control': 'public, s-maxage=300', 'X-Frame-Options': 'DENY' },
    });
  }

  const cacheKey = `search:${vertical}:${q}:${category || 'all'}:${page}:${limit}:${when}:${chips.map(c => c.label).join(',')}:${fast ? 'fast' : 'full'}`;
  if (!isRefresh) {
    const cached = await getCached<any>(cacheKey);
    if (cached) {
      const { cached_at, ...payload } = cached;
      const ageSec = cached_at ? (Date.now() - cached_at) / 1000 : CACHE_TTL_S;
      if (ageSec > SWR_SECONDS && !refreshing.has(cacheKey)) {
        refreshing.add(cacheKey);
        void fetch(url, { headers: { [REFRESH_HEADER]: '1' } })
          .catch(() => { /* background refresh best-effort */ })
          .finally(() => refreshing.delete(cacheKey));
      }
      return new Response(JSON.stringify({ ...payload, search_type: 'cached' }), {
        headers: {
          'Content-Type': 'application/json',
          'Cache-Control': 'public, s-maxage=900, stale-while-revalidate=300',
          'X-Cache': 'HIT',
          'X-Content-Type-Options': 'nosniff',
          'X-Frame-Options': 'DENY',
        },
      });
    }
  }

  await ensureTables();

  const start = Date.now();
  const firstPage = vertical === 'all' && page === 1;
  let related_ms = 0;
  const relatedP = (firstPage && !fast)
    ? (async () => {
        const t = Date.now();
        const r = await relatedSearches(q, 6);
        related_ms = Date.now() - t;
        return r;
      })()
    : Promise.resolve([] as string[]);

  // Dictionary instant answer (DuckDuckGo-style): own budget, parallel from t0
  const dictWord = isDictionaryQuery(ops.cleaned || raw, ops.active, vertical, page)
    ? (ops.cleaned || raw).trim()
    : '';
  let dict_ms = 0;
  const dictP: Promise<DictionaryEntry | null> = dictWord
    ? (async () => {
        const t = Date.now();
        try {
          return await fetchDictionaryBudgeted(dictWord);
        } finally {
          dict_ms = Date.now() - t;
        }
      })()
    : Promise.resolve(null);

  const [fed, related, dictionary] = await Promise.all([
    federatedSearch({ q, vertical, page, limit, ops, when, fast }),
    relatedP,
    dictP,
  ]);

  // Did-you-mean: curated exact corrections always; fuzzy only when the
  // index barely responded (avoids false-positive suggestions on good queries)
  let suggestion = processed.spellingCorrected ? processed.corrected : null;
  if (!suggestion) {
    suggestion = exactCorrection(ops.cleaned || raw) ||
      (fed.results.length < 3 ? didYouMean(ops.cleaned || raw) : null);
  }

  // Dictionary card replaces the featured answer for lookup-style queries
  const answer = firstPage && vertical === 'all' && !dictionary
    ? buildAnswer(q, fed.results)
    : null;

  const paa: PaaItem[] = firstPage && vertical === 'all' && !fast
    ? peopleAlsoAsk(q, fed.results, related, 4)
    : [];

  const response: any = {
    query: q,
    original_query: raw,
    vertical,
    results: fast ? fed.results.slice(0, 5) : fed.results,
    total_results: fed.results.length,
    knowledge_card: fed.knowledge_card,
    providers: fed.providers,
    related,
    pagination: fed.pagination,
    page_size: limit,
    search_type: 'federated',
    duration_ms: Date.now() - start,
    page,
    pages_available: fed.pagination ? maxPages : 1,
    spelling_corrected: processed.spellingCorrected ? processed.corrected : null,
    did_you_mean: suggestion && suggestion !== q ? suggestion : null,
    suggested_category: processed.categoryInferred || null,
    operators: chips,
    when: when || 'any',
    phase: fast ? 'fast' : 'full',
    answer,
    dictionary: dictionary && dictionary.found ? dictionary : null,
    people_also_ask: paa,
    interpretation: buildInterpretation(processed),
    timings: {
      total_ms: Date.now() - start,
      card_ms: fed.card_ms ?? 0,
      related_ms,
      dict_ms,
      slowest_provider_ms: Math.max(0, ...(fed.providers || []).map(p => p.ms)),
    },
  };

  response.cached_at = Date.now();
  await setCache(cacheKey, response, CACHE_TTL_S);

  return new Response(JSON.stringify(response), {
    headers: {
      'Content-Type': 'application/json',
        'Cache-Control': 'public, s-maxage=900, stale-while-revalidate=300',
      'X-Cache': 'MISS',
      'X-Content-Type-Options': 'nosniff',
      'X-Frame-Options': 'DENY',
    },
  });
};
