import { PROVIDER_WEIGHTS } from './types';
import type { ProviderResult, ProviderStat, FederationResponse, Vertical } from './types';
import { screenResults, slopPenalty } from './slop';
import { localSearch, localMedia, localCode } from './local';
import { knowledgeCard } from './wikidata';
import { searchCommons } from './commons';
import { searchOpenverse } from './openverse';
import { searchArchiveImages, searchArchiveVideos } from './archive';
import { searchYouTube } from './youtube';
import { searchGdelt } from './gdelt';
import { searchRss } from './rss';
import { searchOpenAlex, searchCrossref } from './papers';
import { searchCode } from './github';
import { searchHn } from './hn';
import { searchStack } from './stack';
import { searchWikipedia } from './wikipedia';
import { searchCcIndex } from './ccindex';
import { searchOpenStreetMap } from './osm';
import { searchProducts } from './products';
import { searchYandexWeb, searchYandexImages } from './yandex';
import { queryFastTier } from '../fast_tier';
import { getInternalLinks } from '../turso';
import type { SearchFilterOpts } from '../turso';
import type { QueryOps } from '../queryops';
import { matchesSite, matchesFiletype, matchesDates, whenLowerBound } from '../queryops';

const VALID_VERTICALS: Vertical[] = ['all', 'images', 'videos', 'news', 'code', 'maps', 'academic', 'products'];

export function isVertical(value: string): value is Vertical {
  return VALID_VERTICALS.includes(value as Vertical);
}

const PAGE_SIZES: Record<Vertical, number> = {
  all: 20,
  images: 30,
  videos: 20,
  news: 25,
  code: 20,
  maps: 15,
  academic: 20,
  products: 20,
};

interface Task {
  name: string;
  run: () => Promise<ProviderResult[]>;
  /** Per-task budget override — used by Common Crawl (slower index API). */
  budgetMs?: number;
}

interface SettledTask {
  name: string;
  items: ProviderResult[];
  ms: number;
  error?: string;
}

/**
 * Latency budget: no single provider may hold the response hostage.
 * Local results exist for every vertical except news, so external providers
 * only get a best-effort window there — their own cache still warms in the
 * background, and the API's stale-while-revalidate picks them up shortly.
 */
const LOCAL_BUDGET_MS = 300;
const EXTERNAL_ONLY_BUDGET_MS = 2000;
const CARD_BUDGET_MS = 1800;

async function raceBudget<T>(promise: Promise<T>, budgetMs: number): Promise<T | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<null>(resolve => {
    timer = setTimeout(() => resolve(null), budgetMs);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function runTasks(tasks: Task[], budgetMs: number): Promise<SettledTask[]> {
  return Promise.all(
    tasks.map(async task => {
      const start = Date.now();
      const taskBudget = task.budgetMs ?? budgetMs;
      try {
        const raced = await raceBudget(task.run(), taskBudget);
        if (raced === null) {
          return {
            name: task.name,
            items: [] as ProviderResult[],
            ms: Date.now() - start,
            error: `budget exceeded (${taskBudget}ms)`,
          };
        }
        return {
          name: task.name,
          items: (raced || []).filter(Boolean),
          ms: Date.now() - start,
        };
      } catch (e: any) {
        return {
          name: task.name,
          items: [] as ProviderResult[],
          ms: Date.now() - start,
          error: e?.message || 'provider failed',
        };
      }
    })
  );
}

function lexical(text: string, tokens: string[]): number {
  if (!tokens.length) return 0.5;
  const t = (text || '').toLowerCase();
  let hits = 0;
  for (const token of tokens) {
    if (t.includes(token)) hits++;
  }
  return hits / tokens.length;
}

function freshnessFactor(dateStr: string | undefined, type: string): number {
  if (!dateStr) return 0.5;
  const ts = Date.parse(dateStr);
  if (isNaN(ts)) return 0.5;
  const ageDays = Math.max(0, (Date.now() - ts) / 86_400_000);
  const halfLife = type === 'news' ? 14 : type === 'videos' ? 365 : 730;
  return Math.max(0.1, Math.exp(-ageDays / halfLife));
}

function scoreResult(result: ProviderResult, tokens: string[]): number {
  const weight = PROVIDER_WEIGHTS[result.provider] ?? 0.7;

  const relevance = (result.provider === 'local' && typeof result.score === 'number')
    ? result.score
    : (0.60 * lexical(result.title, tokens) +
       0.25 * lexical(result.snippet, tokens) +
       0.15);

  const freshnessWeight =
    result.result_type === 'news' ? 0.35 :
    result.result_type === 'videos' ? 0.15 : 0.10;

  const fresh = freshnessFactor(result.published_date, result.result_type);
  const freshMult = (1 - freshnessWeight) + freshnessWeight * fresh;

  return weight * (0.60 + 0.40 * Math.min(1, relevance)) * freshMult * slopPenalty(result);
}

function dedupeByTitle(items: ProviderResult[]): ProviderResult[] {
  const seen = new Set<string>();
  const kept: ProviderResult[] = [];
  for (const item of items) {
    const key = (item.title || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().slice(0, 80);
    if (!key) { kept.push(item); continue; }
    if (seen.has(key)) continue;
    seen.add(key);
    kept.push(item);
  }
  return kept;
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase().replace(/^www\./, '');
  } catch {
    return '';
  }
}

/**
 * Domain diversity: at most 2 results per host inside the top-10 window.
 * Held-back results are re-appended after the window — never dropped.
 */
function diversify(items: ProviderResult[], windowSize = 10, maxPerDomain = 2): ProviderResult[] {
  const counts = new Map<string, number>();
  const inWindow: ProviderResult[] = [];
  const rest: ProviderResult[] = [];

  items.forEach((item, idx) => {
    const host = hostOf(item.url);
    const n = counts.get(host) || 0;
    if (idx < windowSize && n >= maxPerDomain) {
      rest.push(item);
      return;
    }
    counts.set(host, n + 1);
    inWindow.push(item);
  });

  return [...inWindow, ...rest];
}

/** Sitelinks: sub-pages for the top result of each domain in this page. */
async function attachSitelinks(ordered: ProviderResult[]): Promise<void> {
  const firstOfDomain = new Map<string, ProviderResult>();
  const siblingsByDomain = new Map<string, ProviderResult[]>();

  for (const item of ordered) {
    const host = hostOf(item.url);
    if (!host) continue;
    if (!firstOfDomain.has(host)) firstOfDomain.set(host, item);
    else siblingsByDomain.get(host)?.push(item);
  }

  const tasks: Promise<void>[] = [];
  // Only the top-ranked domains get link-graph lookups — enough for the page
  let leads = 0;
  for (const [host, lead] of firstOfDomain) {
    if (leads >= 5) break;
    leads++;
    if (!lead) continue;
    const siblings = (siblingsByDomain.get(host) || []).filter(s => s.url !== lead.url);
    if (siblings.length >= 2) {
      lead.sitelinks = siblings.slice(0, 4).map(s => ({ title: s.title, url: s.url }));
      continue;
    }
    // Not enough same-domain results here — ask the link graph / index
    tasks.push((async () => {
      const fallback = await getInternalLinks(lead.url, 4);
      const usable = fallback.filter(l => l.url !== lead.url);
      if (usable.length >= 2) lead.sitelinks = usable;
    })());
  }
  await Promise.all(tasks);
}

/** Operators apply to EVERY result, not just local ones (Google semantics). */
function passesOps(item: ProviderResult, ops?: QueryOps, whenFrom?: string): boolean {
  if (ops) {
    if (ops.site && !matchesSite(item.url, ops.site)) return false;
    if (ops.filetype && !matchesFiletype(item.url, ops.filetype)) return false;
    if (!matchesDates(item.published_date || '', ops.dateFrom, ops.dateTo)) return false;
    if (ops.exclude.length) {
      const hay = `${item.title || ''}\n${item.snippet || ''}`.toLowerCase();
      if (ops.exclude.some(t => hay.includes(t.toLowerCase()))) return false;
    }
  }
  if (whenFrom && (item.published_date || '').slice(0, 10) < whenFrom) return false;
  return true;
}

export interface FederatedOptions {
  q: string;
  vertical: Vertical;
  page?: number;
  limit?: number;
  ops?: QueryOps;
  when?: string;
  /** Fast phase: local index + knowledge only (no external providers yet). */
  fast?: boolean;
}

export async function federatedSearch(opts: FederatedOptions): Promise<FederationResponse> {
  const { q, vertical } = opts;
  const page = Math.max(opts.page || 1, 1);
  const pageLimit = Math.min(opts.limit || PAGE_SIZES[vertical], 50);
  const deep = page > 1;
  const tokens = q.toLowerCase().split(/\s+/).filter(t => t.length > 1);

  const tasks: Task[] = [];
  let cardPromise: Promise<ProviderResult | null> = Promise.resolve(null);
  let cardDurationMs: number | null = null;

  const localLimit = deep ? Math.min(pageLimit * page, 100) : Math.min(pageLimit * 2, 100);
  const filterOpts: SearchFilterOpts = { ops: opts.ops, when: opts.when };

  switch (vertical) {
    case 'all':
      tasks.push(
        { name: 'local', run: () => localSearch(q, localLimit, undefined, filterOpts) },
        { name: 'duckdb', run: () => queryFastTier(q, 8) },
        ...(opts.ops?.site && !opts.fast
          ? [{ name: 'ccindex', run: () => searchCcIndex(q, opts.ops!.site, 12), budgetMs: 3000 }]
          : []),
        ...((deep || opts.fast) ? [] : [
          { name: 'yandex', run: () => searchYandexWeb(q, 6) },
          { name: 'openalex', run: () => searchOpenAlex(q, 6, 1) },
          { name: 'crossref', run: () => searchCrossref(q, 6, 1) },
          { name: 'gdelt', run: () => searchGdelt(q, 8, 1) },
          { name: 'wikipedia', run: () => searchWikipedia(q, 6) },
          { name: 'hn', run: () => searchHn(q, 6) },
          { name: 'stack', run: () => searchStack(q, 'stackoverflow', 5) },
        ])
      );
      if (!deep) {
        // Bound the panel from t0 so Wikidata never serializes behind providers
        const tracked = (async () => {
          const t = Date.now();
          try {
            return await knowledgeCard(q);
          } finally {
            cardDurationMs = Date.now() - t;
          }
        })();
        cardPromise = raceBudget(tracked, CARD_BUDGET_MS);
      }
      break;

    case 'images':
      tasks.push(
        { name: 'local', run: () => localMedia(q, 'image', localLimit, filterOpts) },
        ...(deep ? [] : [
          { name: 'yandex_images', run: () => searchYandexImages(q, 24) },
          { name: 'commons', run: () => searchCommons(q, 24, 1) },
          { name: 'openverse', run: () => searchOpenverse(q, 20, 1) },
          { name: 'archive', run: () => searchArchiveImages(q, 12, 1) },
        ])
      );
      break;

    case 'videos':
      tasks.push(
        { name: 'local', run: () => localMedia(q, 'video', localLimit, filterOpts) },
        ...(deep ? [] : [
          { name: 'youtube', run: () => searchYouTube(q, 15, 1) },
          { name: 'archive', run: () => searchArchiveVideos(q, 15, 1) },
        ])
      );
      break;

    case 'news':
      tasks.push(
        { name: 'rss', run: () => searchRss(q, 25, page) },
        { name: 'gdelt', run: () => searchGdelt(q, 20, page) },
        { name: 'hn', run: () => searchHn(q, 12) }
      );
      break;

    case 'code':
      tasks.push(
        { name: 'local', run: () => localCode(q, deep ? 0 : 12, filterOpts) },
        { name: 'github', run: () => searchCode(q, pageLimit, page) },
        { name: 'stack', run: () => searchStack(q, 'stackoverflow', 8) }
      );
      break;

    case 'maps':
      tasks.push(
        { name: 'osm', run: () => searchOpenStreetMap(q, 15) },
        { name: 'local', run: () => localSearch(q, 10, undefined, filterOpts) }
      );
      break;

    case 'academic':
      tasks.push(
        { name: 'openalex', run: () => searchOpenAlex(q, 20, page) },
        { name: 'crossref', run: () => searchCrossref(q, 15, page) },
        { name: 'local', run: () => localSearch(q, 10, 'science_research', filterOpts) }
      );
      break;

    case 'products':
      tasks.push(
        { name: 'products', run: () => searchProducts(q, 25) }
      );
      break;
  }

  const hasLocal = tasks.some(t => t.name === 'local');
  const settled = await runTasks(tasks, hasLocal ? LOCAL_BUDGET_MS : EXTERNAL_ONLY_BUDGET_MS);
  const card = await cardPromise;

  let merged: ProviderResult[] = [];
  for (const task of settled) {
    merged.push(...task.items);
  }

  merged = screenResults(merged);
  if (vertical === 'images' || vertical === 'news') merged = dedupeByTitle(merged);

  for (const item of merged) {
    item.score = scoreResult(item, tokens);
  }
  merged.sort((a, b) => (b.score || 0) - (a.score || 0));

  const whenFrom = whenLowerBound(opts.when || '');
  if (opts.ops?.active || whenFrom) {
    merged = merged.filter(item => passesOps(item, opts.ops, whenFrom));
  }

  const ordered = diversify(merged);
  if (vertical === 'all' && !deep) await attachSitelinks(ordered);

  const offset = deep && vertical !== 'code' ? (page - 1) * pageLimit : 0;
  const results = ordered.slice(offset, offset + pageLimit);

  const providers: ProviderStat[] = settled.map(task => ({
    name: task.name,
    count: task.items.length,
    ms: task.ms,
    ...(task.error ? { error: task.error } : {}),
  }));

  return {
    results,
    knowledge_card: card,
    providers,
    pagination: vertical !== 'news',
    card_ms: cardDurationMs ?? undefined,
  };
}
