import { stripHtml } from './providers/http';
import { getDictionaryCache, putDictionaryCache } from './turso';

/**
 * DuckDuckGo-style dictionary instant answer, backed by Wiktionary:
 * definitions via the REST definition endpoint, IPA + audio filename via the
 * parse API (same Wikimedia infra as the knowledge panel).
 * Results are cached in dictionary_cache (30d hits / 7d misses).
 */

export interface DictDefinition {
  pos: string;
  text: string;
  example?: string;
}

export interface DictionaryEntry {
  word: string;
  phonetic: string;
  audio_url: string;
  definitions: DictDefinition[];
  source: 'wiktionary';
  found: boolean;
}

const WT_BASE = 'https://en.wiktionary.org';
// Wiktionary needs ~1.1–1.8s from this host; budget covers it (one-time per
// word — dictionary_cache makes every later lookup a ms-level cache hit)
const ENTRY_BUDGET_MS = 2500;
const FETCH_TIMEOUT_MS = 2200;
const HIT_TTL_S = 30 * 24 * 3600;
const MISS_TTL_S = 7 * 24 * 3600;

const WT_HEADERS = { 'User-Agent': 'Antirot/2.0 (+https://github.com/BoringRats/ratcrowler)' };

/** A query is a dictionary lookup: a short word (or lowercase two-word
 *  phrase), no operators, on the main vertical's first page. Capitalized
 *  two-word queries ("Alan Turing") are entity lookups, not dictionary ones. */
export function isDictionaryQuery(
  cleaned: string,
  hasOps: boolean,
  vertical: string,
  page: number
): boolean {
  if (vertical !== 'all' || page !== 1 || hasOps) return false;
  const w = cleaned.trim();
  if (w.length < 2 || w.length > 24) return false;
  if (!/^[a-zA-Z][a-zA-Z'’-]*(?:\s[a-zA-Z][a-zA-Z'’-]*)?$/.test(w)) return false;
  if (w.includes(' ') && w !== w.toLowerCase()) return false;
  return true;
}

interface WtSense {
  partOfSpeech?: string;
  definitions?: { definition?: string; examples?: (string | { text?: string })[] }[];
}

/** Map the Wiktionary REST definition payload to our shape. */
export function mapWiktionary(payload: any, word: string): DictionaryEntry | null {
  const senses: WtSense[] = payload?.en;
  if (!Array.isArray(senses) || !senses.length) return null;

  const definitions: DictDefinition[] = [];
  for (const block of senses) {
    const pos = (block.partOfSpeech || '').toLowerCase();
    for (const d of (block.definitions || []).slice(0, 2)) {
      const text = stripHtml(d.definition || '').replace(/\s+/g, ' ').trim();
      if (!text) continue;
      const rawExample = Array.isArray(d.examples) ? d.examples[0] : undefined;
      const example = rawExample
        ? (typeof rawExample === 'string' ? rawExample : rawExample.text || '')
        : '';
      definitions.push({
        pos,
        text,
        ...(example ? { example: example.trim() } : {}),
      });
      if (definitions.length >= 8) break;
    }
    if (definitions.length >= 8) break;
  }
  if (!definitions.length) return null;

  return {
    word,
    phonetic: '',
    audio_url: '',
    definitions,
    source: 'wiktionary',
    found: true,
  };
}

/** First English IPA transcription, e.g. /ˈhjuː.mən/ */
export function extractIpa(wikitext: string): string {
  const templates = wikitext.match(/\{\{(?:IPA|IPA-all)\|en\|[^}]*\}\}/g) || [];
  for (const tpl of templates) {
    const m = tpl.match(/(\/[^/{}\n]{1,40}\/|\[[^[\]{}\n]{1,40}\])/);
    if (m) return m[1];
  }
  return '';
}

/** First English pronunciation audio filename, e.g. en-us-human.ogg */
export function extractAudioFile(wikitext: string): string {
  const files = wikitext.match(/\{\{audio\|en\|([^|}]+\.(?:ogg|mp3|wav|flac))/gi) || [];
  for (const f of files) {
    const name = (f.match(/\{\{audio\|en\|([^|}]+)/i) || [])[1]?.trim();
    if (name) return name.replace(/ /g, '_');
  }
  return '';
}

export function audioFileUrl(fileName: string): string {
  return `https://commons.wikimedia.org/wiki/Special:Redirect/file/${encodeURIComponent(fileName)}`;
}

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

async function fetchPronunciation(
  word: string
): Promise<{ phonetic: string; audio_url: string; status: 'ok' | 'error' }> {
  try {
    const res = await fetch(
      `${WT_BASE}/w/api.php?action=parse&page=${encodeURIComponent(word)}` +
      `&prop=wikitext&format=json&redirects=1`,
      { headers: WT_HEADERS, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) }
    );
    if (!res.ok) return { phonetic: '', audio_url: '', status: 'error' };
    const data = (await res.json()) as any;
    // A missing page is a legitimate "no pronunciation", not an error
    const wikitext = data?.parse?.wikitext?.['*'] || '';
    const phonetic = extractIpa(wikitext);
    const audioFile = extractAudioFile(wikitext);
    return { phonetic, audio_url: audioFile ? audioFileUrl(audioFile) : '', status: 'ok' };
  } catch {
    return { phonetic: '', audio_url: '', status: 'error' };
  }
}

/**
 * Look up a word: cache → Wiktionary (definitions ∥ pronunciation) → null.
 * Only DEFINITE misses are negative-cached: a network failure must never
 * suppress the card for a week.
 */
export async function fetchDictionary(rawWord: string): Promise<DictionaryEntry | null> {
  const word = rawWord.trim();
  if (!word) return null;
  const key = word.toLowerCase();

  const cached = await getDictionaryCache(key);
  if (cached) {
    if (!cached.found) return null;
    try {
      return JSON.parse(cached.payload) as DictionaryEntry;
    } catch { /* corrupted cache → refetch */ }
  }

  let defStatus: 'ok' | 'notfound' | 'error' = 'error';
  const defPromise = (async () => {
    try {
      const res = await fetch(
        `${WT_BASE}/api/rest_v1/page/definition/${encodeURIComponent(word)}`,
        { headers: WT_HEADERS, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) }
      );
      if (res.status === 404) { defStatus = 'notfound'; return null; }
      if (!res.ok) { defStatus = 'error'; return null; }
      const data = await res.json();
      defStatus = 'ok';
      return data;
    } catch {
      defStatus = 'error';
      return null;
    }
  })();

  const [defRes, pron] = await Promise.all([defPromise, fetchPronunciation(word)]);

  const entry = mapWiktionary(defRes, word);
  if (!entry) {
    // Only cache the miss when upstream actually answered — never on failures
    if (defStatus !== 'error' && pron.status !== 'error') {
      await putDictionaryCache(key, null, false, MISS_TTL_S);
    }
    return null;
  }
  entry.phonetic = pron.phonetic;
  entry.audio_url = pron.audio_url;

  // Skip caching a half-fetched entry if pronunciation errored (audio/IPA
  // would be missing for a month) — but definitions-only is fine on 404 pages
  if (pron.status !== 'error') {
    await putDictionaryCache(key, entry, true, HIT_TTL_S);
  }
  return entry;
}

/** Fetch with a hard latency budget — never blocks the search response. */
export async function fetchDictionaryBudgeted(word: string): Promise<DictionaryEntry | null> {
  return raceBudget(fetchDictionary(word), ENTRY_BUDGET_MS);
}
