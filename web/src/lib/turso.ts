import { createClient } from '@libsql/client';
import {
  parseQueryOps, matchesFiletype, matchesSite, matchesDates, whenLowerBound,
} from './queryops';
import type { QueryOps } from './queryops';

let _client: ReturnType<typeof createClient> | null = null;

export function getTurso() {
  if (!_client) {
    const url = import.meta.env.TURSO_DATABASE_URL || '';
    const token = import.meta.env.TURSO_AUTH_TOKEN || '';

    if (url && token) {
      _client = createClient({ url, authToken: token });
    } else {
      const path = import.meta.env.LOCAL_DB_PATH || '../search/data/local.db';
      _client = createClient({ url: `file:${path}` });
    }
  }
  return _client;
}

export function isTursoConfigured(): boolean {
  return !!(import.meta.env.TURSO_DATABASE_URL && import.meta.env.TURSO_AUTH_TOKEN);
}

let _tablesReady = false;

// FTS5 BM25 field weights: title, source_name, content_text, author
const BM25_WEIGHTS = [3.0, 1.5, 1.0, 0.5];

const FTS_TRIGGERS = [
  `CREATE TRIGGER IF NOT EXISTS knowledge_items_ai AFTER INSERT ON knowledge_items BEGIN
     INSERT INTO knowledge_fts(rowid, title, source_name, content_text, author)
     VALUES (new.id, new.title, new.source_name, new.content_text, COALESCE(new.author, ''));
   END`,
  `CREATE TRIGGER IF NOT EXISTS knowledge_items_ad AFTER DELETE ON knowledge_items BEGIN
     INSERT INTO knowledge_fts(knowledge_fts, rowid, title, source_name, content_text, author)
     VALUES ('delete', old.id, old.title, old.source_name, old.content_text, COALESCE(old.author, ''));
   END`,
  `CREATE TRIGGER IF NOT EXISTS knowledge_items_au AFTER UPDATE ON knowledge_items BEGIN
     INSERT INTO knowledge_fts(knowledge_fts, rowid, title, source_name, content_text, author)
     VALUES ('delete', old.id, old.title, old.source_name, old.content_text, COALESCE(old.author, ''));
     INSERT INTO knowledge_fts(rowid, title, source_name, content_text, author)
     VALUES (new.id, new.title, new.source_name, new.content_text, COALESCE(new.author, ''));
   END`,
];

export async function ensureTables() {
  if (_tablesReady) return;
  const db = getTurso();
  try {
    try {
      await db.execute('PRAGMA journal_mode = WAL;');
      await db.execute('PRAGMA synchronous = NORMAL;');
      await db.execute('PRAGMA mmap_size = 268435456;');
      await db.execute('PRAGMA cache_size = -32000;');
    } catch { /* Remote Turso ignores local file pragmas */ }

    await db.execute(`
      CREATE TABLE IF NOT EXISTS knowledge_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        source_name TEXT DEFAULT '',
        source_category TEXT DEFAULT '',
        content_text TEXT DEFAULT '',
        content_hash TEXT,
        author TEXT DEFAULT '',
        published_date TEXT DEFAULT '',
        language TEXT DEFAULT 'en',
        word_count INTEGER DEFAULT 0,
        quality_score REAL DEFAULT 0.0,
        overall_rank REAL DEFAULT 0.0,
        authority_score REAL DEFAULT 0.0,
        freshness_score REAL DEFAULT 0.0,
        engagement_score REAL DEFAULT 0.0,
        media_json TEXT DEFAULT '',
        slop_score REAL DEFAULT 0.0,
        slop_verdict TEXT DEFAULT 'pass',
        slop_signals TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
      )
    `);

    // Migrate older databases missing newer columns
    try {
      const info = await db.execute('PRAGMA table_info(knowledge_items)');
      const columns = new Set(info.rows.map((r: any) => r.name));
      const migrations: [string, string][] = [
        ['authority_score', 'REAL DEFAULT 0.0'],
        ['freshness_score', 'REAL DEFAULT 0.0'],
        ['engagement_score', 'REAL DEFAULT 0.0'],
        ['media_json', "TEXT DEFAULT ''"],
        ['slop_score', 'REAL DEFAULT 0.0'],
        ['slop_verdict', "TEXT DEFAULT 'pass'"],
        ['slop_signals', "TEXT DEFAULT ''"],
      ];
      for (const [column, ddl] of migrations) {
        if (!columns.has(column)) {
          try { await db.execute(`ALTER TABLE knowledge_items ADD COLUMN ${column} ${ddl}`); } catch { /* already added */ }
        }
      }
    } catch { /* PRAGMA unsupported */ }

    // Slop gate audit trail — every rejection, at every stage
    await db.execute(`
      CREATE TABLE IF NOT EXISTS slop_rejects (
        url TEXT PRIMARY KEY,
        score REAL DEFAULT 0.0,
        signals TEXT DEFAULT '',
        source_name TEXT DEFAULT '',
        stage TEXT DEFAULT 'index',
        detected_at TEXT DEFAULT (datetime('now'))
      )
    `);

    // Knowledge panel entity cache (Wikidata claims, 7-day TTL)
    await db.execute(`
      CREATE TABLE IF NOT EXISTS knowledge_entities (
        qid TEXT PRIMARY KEY,
        label TEXT DEFAULT '',
        description TEXT DEFAULT '',
        entity_type TEXT DEFAULT '',
        props_json TEXT DEFAULT '',
        image_url TEXT DEFAULT '',
        wiki_url TEXT DEFAULT '',
        query_key TEXT DEFAULT '',
        updated_at TEXT DEFAULT (datetime('now'))
      )
    `);
    await db.execute('CREATE INDEX IF NOT EXISTS idx_entities_query ON knowledge_entities(query_key)');

    // Dictionary instant-answer cache (hits 30d / misses 7d — see dictionary.ts)
    await db.execute(`
      CREATE TABLE IF NOT EXISTS dictionary_cache (
        word TEXT PRIMARY KEY,
        payload TEXT NOT NULL DEFAULT 'null',
        found INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now'))
      )
    `);

    try {
      await db.execute(`
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
          title, source_name, content_text, author,
          content='knowledge_items', content_rowid='id',
          tokenize='porter unicode61'
        )
      `);
      for (const trigger of FTS_TRIGGERS) {
        try { await db.execute(trigger); } catch { /* trigger exists */ }
      }
    } catch { /* FTS5 not available */ }

    await db.execute('CREATE INDEX IF NOT EXISTS idx_ki_category ON knowledge_items(source_category)');
    await db.execute('CREATE INDEX IF NOT EXISTS idx_ki_source ON knowledge_items(source_name)');
    await db.execute('CREATE INDEX IF NOT EXISTS idx_ki_quality ON knowledge_items(quality_score DESC)');
    await db.execute('CREATE INDEX IF NOT EXISTS idx_ki_rank ON knowledge_items(overall_rank DESC)');

    // Hot-path read tuning (applies to the file-backed local index)
    try {
      await db.execute('PRAGMA mmap_size = 268435456');
      await db.execute('PRAGMA cache_size = -16000');
      await db.execute('PRAGMA temp_store = MEMORY');
    } catch { /* remote Turso — PRAGMA not applicable */ }
  } catch (e) {
    console.error('Table creation error:', e);
  }
  _tablesReady = true;
}

// ============================================================
// QUERY PROCESSING (client-side, classical IR)
// ============================================================

const STOP_WORDS = new Set([
  'a','an','the','and','or','but','in','on','at','to','for','of','with','by',
  'from','as','is','was','are','were','be','been','being','have','has','had',
  'do','does','did','will','would','could','should','may','might','shall',
  'can','this','that','these','those','i','you','he','she','it','we','they',
  'what','which','who','whom','when','where','why','how','all','each','every',
  'both','few','more','most','other','some','such','no','not','only','own',
  'same','so','than','too','very','just','because','if','about','up','out',
  'then','here','there','also','into','over','after','before','between','under',
]);

const SPELLING_CORRECTIONS: Record<string, string> = {
  'reciepe': 'recipe', 'recipie': 'recipe', 'recepie': 'recipe',
  'algorith': 'algorithm', 'algorithim': 'algorithm', 'algoritm': 'algorithm',
  'pythn': 'python', 'pyhton': 'python',
  'javscript': 'javascript', 'javasript': 'javascript',
  'mathmatics': 'mathematics', 'mathematics': 'mathematics',
  'phisics': 'physics', 'phsyics': 'physics',
  'chmistry': 'chemistry', 'chemestry': 'chemistry',
  'progrmming': 'programming', 'programing': 'programming',
  'nutriton': 'nutrition', 'histroy': 'history',
  'enviroment': 'environment', 'enviorment': 'environment',
  'philosphy': 'philosophy', 'psycology': 'psychology',
  'ecnomics': 'economics', 'sustanable': 'sustainable',
  'artifical intelligence': 'artificial intelligence',
  'machine lerning': 'machine learning',
  'protien': 'protein', 'vitiman': 'vitamin',
  'evoluton': 'evolution', 'clmate change': 'climate change',
};

const SYNONYMS: Record<string, string[]> = {
  'recipe': ['cooking', 'food', 'meal'],
  'cooking': ['recipe', 'food', 'kitchen'],
  'algorithm': ['method', 'procedure', 'technique'],
  'science': ['research', 'study', 'experiment'],
  'math': ['mathematics', 'calculation', 'arithmetic'],
  'history': ['past', 'ancient', 'historical'],
  'philosophy': ['thought', 'reasoning', 'logic', 'ethics'],
  'programming': ['coding', 'development', 'software'],
  'nutrition': ['diet', 'food', 'health'],
  'physics': ['mechanics', 'thermodynamics', 'quantum'],
  'chemistry': ['elements', 'reactions', 'compounds'],
  'biology': ['life', 'organisms', 'genetics', 'ecology'],
  'psychology': ['mind', 'behavior', 'cognitive'],
  'astronomy': ['space', 'stars', 'planets', 'cosmos'],
  'medicine': ['health', 'medical', 'treatment'],
  'economics': ['finance', 'market', 'trade'],
};

const CATEGORY_KEYWORDS: Record<string, string[]> = {
  'science_research': [
    'research', 'study', 'experiment', 'hypothesis', 'theory', 'journal',
    'peer-reviewed', 'paper', 'scientific', 'laboratory', 'empirical',
  ],
  'programming_engineering': [
    'code', 'programming', 'software', 'developer', 'api', 'database',
    'algorithm', 'python', 'javascript', 'html', 'css', 'git', 'linux',
  ],
  'university_learning': [
    'course', 'lecture', 'textbook', 'curriculum', 'syllabus', 'exam',
    'university', 'college', 'degree', 'semester', 'class',
  ],
  'knowledge_foundations': [
    'encyclopedia', 'history', 'philosophy', 'geography', 'culture',
    'religion', 'mythology', 'art', 'music', 'literature', 'language',
  ],
  'data_economics': [
    'data', 'statistics', 'economics', 'finance', 'market', 'gdp',
    'demographics', 'census', 'survey', 'chart', 'graph', 'dataset',
  ],
  'deep_thinking': [
    'logic', 'reasoning', 'argument', 'debate', 'analysis', 'critique',
    'essay', 'opinion', 'perspective', 'theory', 'framework', 'model',
  ],
};

export function inferCategory(tokens: string[], original: string): string | null {
  const text = `${tokens.join(' ')} ${original}`.toLowerCase();
  let best: string | null = null;
  let bestScore = 0;
  for (const [category, keywords] of Object.entries(CATEGORY_KEYWORDS)) {
    const score = keywords.reduce((n, kw) => (text.includes(kw) ? n + 1 : n), 0);
    if (score > bestScore) {
      bestScore = score;
      best = category;
    }
  }
  return best;
}

export function processQuery(raw: string): {
  original: string;
  normalized: string;
  corrected: string;
  tokens: string[];
  expandedTokens: string[];
  ftsQuery: string;
  expandedFts: string;
  spellingCorrected: boolean;
  categoryInferred: string | null;
} {
  const original = raw.trim();
  const normalized = original.toLowerCase().replace(/[^\w\s\-']/g, ' ').replace(/\s+/g, ' ').trim();

  // Spelling correction
  const words = normalized.split(' ');
  const correctedWords = words.map(w => SPELLING_CORRECTIONS[w] || w);
  const corrected = correctedWords.join(' ');
  const spellingCorrected = normalized !== corrected;

  // Tokenize (remove stop words)
  const tokens = corrected.split(' ').filter(w => w.length > 1 && !STOP_WORDS.has(w));

  // Expand with synonyms
  const expanded = [...tokens];
  for (const token of tokens) {
    const syns = SYNONYMS[token];
    if (syns) {
      for (const syn of syns.slice(0, 2)) {
        if (!expanded.includes(syn)) expanded.push(syn);
      }
    }
  }

  return {
    original,
    normalized,
    corrected,
    tokens,
    expandedTokens: expanded,
    ftsQuery: buildFtsTerms(tokens),
    expandedFts: buildFtsTerms(expanded),
    spellingCorrected,
    categoryInferred: inferCategory(tokens, original),
  };
}

// ============================================================
// FTS QUERY BUILDER
// ============================================================

/**
 * Build an FTS5 MATCH expression.
 * Multi-word queries become a quoted phrase first, then an OR fallback —
 * this gives Google-like "phrase wins" behaviour without dense scoring hacks.
 */
export function buildFtsTerms(tokens: string[]): string {
  const safe = tokens
    .map(t => t.replace(/["*()^:{}[\]]/g, '').trim())
    .filter(t => t.length > 1);
  if (!safe.length) return '';
  if (safe.length === 1) return `"${safe[0]}"`;
  return `"${safe.join(' ')}" OR (${safe.map(t => `"${t}"`).join(' OR ')})`;
}

// ============================================================
// SNIPPET GENERATION
// ============================================================

export function generateSnippet(
  content: string,
  queryTokens: string[],
  maxLength = 300
): string {
  if (!content) return '';

  const sentences = content.split(/(?<=[.!?])\s+/).filter(s => s.trim().length > 10);
  if (!sentences.length) return content.slice(0, maxLength);

  const querySet = new Set(queryTokens.map(t => t.toLowerCase()));

  // Score sentences by query overlap + position
  const scored = sentences.map((sent, i) => {
    const words = new Set(sent.toLowerCase().match(/\w+/g) || []);
    let overlap = 0;
    for (const w of querySet) { if (words.has(w)) overlap++; }
    const positionBonus = 1 / (1 + i * 0.1);
    return { score: overlap + positionBonus, index: i, sent };
  });

  scored.sort((a, b) => b.score - a.score);
  const top = scored.slice(0, 3).sort((a, b) => a.index - b.index);

  let snippet = top.map(t => t.sent).join(' ');
  if (snippet.length > maxLength) {
    snippet = snippet.slice(0, maxLength).replace(/\s+\S*$/, '') + '...';
  }
  return snippet;
}

// ============================================================
// HIGHLIGHT MATCHING TEXT
// ============================================================

export function highlightTerms(text: string, queryTokens: string[]): string {
  if (!text || !queryTokens.length) return text;
  let result = text;
  for (const token of queryTokens) {
    const regex = new RegExp(`\\b(${token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})\\b`, 'gi');
    result = result.replace(regex, '<mark>$1</mark>');
  }
  return result;
}

// ============================================================
// SEARCH FUNCTIONS
// ============================================================

export function cleanSearchText(text: string): string {
  return text
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .slice(0, 5000);
}

export interface SearchResult {
  id: number;
  url: string;
  title: string;
  source_name: string;
  source_category: string;
  content_snippet: string;
  author: string;
  published_date: string;
  word_count: number;
  quality_score: number;
  overall_rank: number;
  authority_score: number;
  freshness_score: number;
  similarity_score: number;
  search_type: string;
  media?: any[];
}

// ============================================================
// TRUSTED DOMAINS (static authority signal — no ML)
// ============================================================

const TRUSTED_DOMAINS: Record<string, number> = {
  'wikipedia.org': 1.0, 'britannica.com': 0.98, 'stanford.edu': 0.97,
  'arxiv.org': 0.96, 'nature.com': 0.98, 'science.org': 0.98,
  'mit.edu': 0.97, 'harvard.edu': 0.97, 'yale.edu': 0.96,
  'github.com': 0.90, 'stackoverflow.com': 0.92,
  'mozilla.org': 0.95, 'developer.mozilla.org': 0.96,
  'loc.gov': 0.98, 'nasa.gov': 0.97,
  'gutenberg.org': 0.95, 'archive.org': 0.95,
  'ourworldindata.org': 0.96, 'worldbank.org': 0.95,
  'khanacademy.org': 0.95, 'ocw.mit.edu': 0.97,
  'mayoclinic.org': 0.97, 'nhs.uk': 0.96,
  'imslp.org': 0.93, 'oeis.org': 0.94,
  'pubchem.ncbi.nlm.nih.gov': 0.96, 'eol.org': 0.94,
  'free.law': 0.93, 'law.cornell.edu': 0.95,
  'instructables.com': 0.88, 'permies.com': 0.87,
  'freecodecamp.org': 0.91, 'investopedia.com': 0.88,
};

export function getDomainAuthority(url: string): number {
  try {
    const domain = new URL(url).hostname.toLowerCase();
    for (const [trusted, score] of Object.entries(TRUSTED_DOMAINS)) {
      if (domain === trusted || domain.endsWith('.' + trusted)) return score;
    }
  } catch { /* malformed URL */ }
  return 0.5;
}

function freshnessFromDate(published: string): number {
  if (!published) return 0.5;
  const ts = Date.parse(published);
  if (isNaN(ts)) return 0.5;
  const ageDays = (Date.now() - ts) / 86_400_000;
  if (ageDays < 0) return 0.5;
  return Math.max(0.1, Math.exp(-ageDays / 365));
}

export interface SearchFilterOpts {
  ops?: QueryOps;
  when?: string;
}

function passesRowFilters(
  row: { url?: string; published?: string },
  ops: QueryOps,
  whenFrom: string,
): boolean {
  const published = row.published || '';
  if (ops.site && !matchesSite(row.url || '', ops.site)) return false;
  if (ops.filetype && !matchesFiletype(row.url || '', ops.filetype)) return false;
  if (!matchesDates(published, ops.dateFrom, ops.dateTo)) return false;
  if (whenFrom && published.slice(0, 10) < whenFrom) return false;
  return true;
}

function excludedHit(row: { title?: string; content?: string }, ops: QueryOps): boolean {
  if (!ops.exclude.length) return false;
  const haystack = `${row.title || ''}\n${row.content || ''}`.toLowerCase();
  return ops.exclude.some(term => haystack.includes(term.toLowerCase()));
}

const BROWSE_COLUMNS = `
  SELECT ki.id, ki.url, ki.title, ki.source_name, ki.source_category,
         ki.content_text, ki.author, ki.published_date, ki.word_count,
         ki.quality_score, ki.overall_rank, ki.authority_score, ki.freshness_score,
         ki.media_json, 0 AS bm25_rank
  FROM knowledge_items ki
`;

/** Operator-only queries (e.g. `site:arxiv.org`) browse the index directly. */
async function browseRows(ops: QueryOps, category: string | undefined, fetchLimit: number): Promise<any[]> {
  const db = getTurso();
  let sql = BROWSE_COLUMNS;
  const args: any[] = [];
  const where: string[] = [];
  if (ops.site) {
    where.push('(LOWER(ki.url) LIKE ? OR LOWER(ki.url) LIKE ?)');
    // coarse prefilter — exact host matching happens in passesRowFilters
    args.push(`%://${ops.site}/%`, `%://${ops.site}`);
  }
  if (ops.filetype) {
    where.push('LOWER(ki.url) LIKE ?');
    args.push(`%.${ops.filetype}`);
  }
  if (where.length) sql += ` WHERE ${where.join(' AND ')}`;
  if (category) { sql += where.length ? ' AND ki.source_category = ?' : ' WHERE ki.source_category = ?'; args.push(category); }
  sql += ' ORDER BY ki.overall_rank DESC LIMIT ?';
  args.push(fetchLimit);
  try {
    return (await db.execute({ sql, args })).rows as any[];
  } catch {
    return [];
  }
}

export async function keywordSearch(
  query: string,
  category?: string,
  limit = 20,
  opts?: SearchFilterOpts
): Promise<SearchResult[]> {
  const ops = opts?.ops ?? parseQueryOps(query);
  const whenFrom = whenLowerBound(opts?.when || '');
  const hasFilters = ops.active || !!whenFrom;

  const processed = processQuery(ops.cleaned || query);
  const words = processed.tokens.length ? processed.tokens : [processed.normalized];
  const hasTerms = words.some(w => w.trim().length > 0);
  if (!hasTerms && !hasFilters) return [];

  const db = getTurso();
  const fetchLimit = Math.min(limit * 5, 500);
  let rows: any[] = [];

  // Forced phrases become required AND clauses; everything else stays phrase-first
  const forcedPhrases = ops.phrases
    .map(p => `"${p.replace(/["*()^:{}[\]]/g, '').trim()}"`)
    .filter(p => p.length > 3);
  const primaryFts = forcedPhrases.length
    ? [...forcedPhrases, processed.ftsQuery].filter(Boolean).join(' AND ')
    : processed.ftsQuery;

  // Operator-only query → browse mode
  if (!hasTerms && hasFilters) {
    rows = await browseRows(ops, category, fetchLimit);
  }

  // FTS5 with phrase-first query and field-weighted BM25
  if (hasTerms && primaryFts) {
    try {
      let sql = `
        SELECT ki.id, ki.url, ki.title, ki.source_name, ki.source_category,
               ki.content_text, ki.author, ki.published_date, ki.word_count,
               ki.quality_score, ki.overall_rank, ki.authority_score, ki.freshness_score,
               ki.media_json,
               bm25(knowledge_fts, ?, ?, ?, ?) AS bm25_rank
        FROM knowledge_fts fts
        JOIN knowledge_items ki ON ki.id = fts.rowid
        WHERE knowledge_fts MATCH ?
      `;
      const args: any[] = [...BM25_WEIGHTS, primaryFts];
      if (category) { sql += ` AND ki.source_category = ?`; args.push(category); }
      sql += ` ORDER BY bm25_rank LIMIT ?`;
      args.push(fetchLimit);
      rows = (await db.execute({ sql, args })).rows;
    } catch { /* fall through to LIKE */ }
  }

  // Fallback: phrase query returned nothing → OR query
  // (skipped when forced phrases exist — an OR fallback would violate them)
  if (!rows.length && hasTerms && !forcedPhrases.length && processed.expandedFts) {
    try {
      let sql = `
        SELECT ki.id, ki.url, ki.title, ki.source_name, ki.source_category,
               ki.content_text, ki.author, ki.published_date, ki.word_count,
               ki.quality_score, ki.overall_rank, ki.authority_score, ki.freshness_score,
               ki.media_json,
               bm25(knowledge_fts, ?, ?, ?, ?) AS bm25_rank
        FROM knowledge_fts fts
        JOIN knowledge_items ki ON ki.id = fts.rowid
        WHERE knowledge_fts MATCH ?
      `;
      const args: any[] = [...BM25_WEIGHTS, processed.expandedFts];
      if (category) { sql += ` AND ki.source_category = ?`; args.push(category); }
      sql += ` ORDER BY bm25_rank LIMIT ?`;
      args.push(fetchLimit);
      rows = (await db.execute({ sql, args })).rows;
    } catch { /* fall through to LIKE */ }
  }

  // Last resort: LIKE search (case-insensitive)
  if (!rows.length && hasTerms && !forcedPhrases.length) {
    const allTokens = [...new Set([...words, ...processed.expandedTokens])];
    const likeConditions = allTokens.map(() =>
      `(LOWER(ki.title) LIKE ? OR LOWER(ki.content_text) LIKE ? OR LOWER(ki.source_name) LIKE ?)`
    ).join(' OR ');
    const likeArgs: any[] = [];
    for (const w of allTokens) {
      likeArgs.push(`%${w.toLowerCase()}%`, `%${w.toLowerCase()}%`, `%${w.toLowerCase()}%`);
    }

    let sql = `
      SELECT ki.id, ki.url, ki.title, ki.source_name, ki.source_category,
             ki.content_text, ki.author, ki.published_date, ki.word_count,
             ki.quality_score, ki.overall_rank, ki.authority_score, ki.freshness_score,
             ki.media_json, 0 AS bm25_rank
      FROM knowledge_items ki
      WHERE ${likeConditions}
    `;
    if (category) { sql += ` AND ki.source_category = ?`; likeArgs.push(category); }
    sql += ` ORDER BY ki.overall_rank DESC LIMIT ?`;
    likeArgs.push(fetchLimit);

    rows = (await db.execute({ sql, args: likeArgs })).rows;
  }

  if (!rows.length) return [];

  // Operator filters: site / filetype / after: / before: / when / exclusions
  if (hasFilters) {
    rows = rows.filter(row =>
      passesRowFilters({ url: row.url, published: row.published_date }, ops, whenFrom) &&
      !excludedHit({ title: row.title, content: row.content_text }, ops)
    );
  }
  if (!rows.length) return [];

  // Normalize BM25 (lower = better, negative values) to 0..1
  const rawScores = rows.map((r: any) => -(r.bm25_rank || 0));
  const maxScore = Math.max(...rawScores, 0.0001);

  const scored = rows.map((row: any, i: number) => {
    const textScore = rawScores[i] / maxScore;
    const authority = (row.authority_score || 0) || getDomainAuthority(row.url || '');
    const freshness = (row.freshness_score || 0) || freshnessFromDate(row.published_date || '');
    const quality = row.quality_score || 0.5;

    // Composite score: text relevance dominant, static signals as tie-breakers
    const finalScore =
      0.50 * textScore +
      0.20 * authority +
      0.15 * quality +
      0.15 * freshness;

    const snippet = generateSnippet(row.content_text || '', words);

    return {
      id: row.id,
      url: row.url,
      title: row.title,
      source_name: row.source_name,
      source_category: row.source_category,
      content_snippet: snippet,
      author: row.author || '',
      published_date: row.published_date || '',
      word_count: row.word_count || 0,
      quality_score: quality,
      overall_rank: row.overall_rank || 0,
      authority_score: authority,
      freshness_score: freshness,
      similarity_score: finalScore,
      search_type: 'keyword',
      media: parseMedia(row.media_json),
    } as SearchResult;
  });

  scored.sort((a, b) => b.similarity_score - a.similarity_score);
  return scored.slice(0, limit);
}

export function parseMedia(mediaJson: string | null | undefined): any[] {
  if (!mediaJson) return [];
  try {
    const parsed = JSON.parse(mediaJson);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// ============================================================
// KNOWLEDGE PANEL ENTITY CACHE (7-day TTL)
// ============================================================

export interface EntityCacheRow {
  qid: string;
  label: string;
  description: string;
  entity_type: string;
  props_json: string;
  image_url: string;
  wiki_url: string;
  query_key: string;
  updated_at?: string;
}

const ENTITY_TTL_MS = 7 * 24 * 3600 * 1000;

// ============================================================
// DICTIONARY CACHE (instant answer, hits 30d / misses 7d)
// ============================================================

export interface DictionaryCacheRow {
  word: string;
  payload: string;
  found: number;
  updated_at?: string;
}

export async function getDictionaryCache(word: string): Promise<DictionaryCacheRow | null> {
  const db = getTurso();
  try {
    const res = await db.execute({
      sql: 'SELECT word, payload, found, updated_at FROM dictionary_cache WHERE word = ? LIMIT 1',
      args: [word],
    });
    const row: any = res.rows[0];
    if (!row) return null;
    const updated = Date.parse(`${String(row.updated_at || '').replace(' ', 'T')}Z`);
    if (isNaN(updated)) return null;
    const ttlMs = (row.found ? 30 : 7) * 24 * 3600 * 1000;
    if (Date.now() - updated > ttlMs) return null;
    return row as DictionaryCacheRow;
  } catch {
    return null;
  }
}

export async function putDictionaryCache(
  word: string,
  entry: unknown,
  found: boolean,
  ttlSeconds: number
): Promise<void> {
  const db = getTurso();
  try {
    await db.execute({
      sql: `INSERT INTO dictionary_cache (word, payload, found, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(word) DO UPDATE SET
              payload=excluded.payload, found=excluded.found, updated_at=datetime('now')`,
      args: [word, JSON.stringify(entry ?? null), found ? 1 : 0],
    });
    void ttlSeconds; // TTL enforced on read (row expiry checked against found flag)
  } catch { /* cache write is best-effort */ }
}

export async function getCachedEntity(queryKey: string): Promise<EntityCacheRow | null> {
  const db = getTurso();
  try {
    const res = await db.execute({
      sql: 'SELECT * FROM knowledge_entities WHERE query_key = ? LIMIT 1',
      args: [queryKey],
    });
    const row: any = res.rows[0];
    if (!row) return null;
    const updated = Date.parse(`${String(row.updated_at || '').replace(' ', 'T')}Z`);
    if (isNaN(updated) || Date.now() - updated > ENTITY_TTL_MS) return null;
    return row as EntityCacheRow;
  } catch {
    return null;
  }
}

export async function putCachedEntity(row: Omit<EntityCacheRow, 'updated_at'>): Promise<void> {
  const db = getTurso();
  try {
    await db.execute({
      sql: `INSERT INTO knowledge_entities
              (qid, label, description, entity_type, props_json, image_url, wiki_url, query_key, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(qid) DO UPDATE SET
              label=excluded.label, description=excluded.description,
              entity_type=excluded.entity_type, props_json=excluded.props_json,
              image_url=excluded.image_url, wiki_url=excluded.wiki_url,
              query_key=excluded.query_key, updated_at=datetime('now')`,
      args: [row.qid, row.label, row.description, row.entity_type,
             row.props_json, row.image_url, row.wiki_url, row.query_key],
    });
  } catch { /* cache write is best-effort */ }
}

/**
 * Sitelink candidates for a result: same-host pages it links to (page_links),
 * falling back to other indexed pages on that host ordered by rank.
 */
export async function getInternalLinks(url: string, limit = 6): Promise<{ title: string; url: string }[]> {
  const db = getTurso();
  let host = '';
  try { host = new URL(url).hostname.toLowerCase().replace(/^www\./, ''); } catch { return []; }
  if (!host) return [];

  const isSameHost = (u: string): boolean => {
    try {
      const h = new URL(u).hostname.toLowerCase().replace(/^www\./, '');
      return h === host || h.endsWith(`.${host}`);
    } catch { return false; }
  };

  const out: { title: string; url: string }[] = [];
  try {
    const res = await db.execute({
      sql: 'SELECT dst_url FROM page_links WHERE src_url = ? LIMIT 100',
      args: [url],
    });
    for (const row of res.rows) {
      const dst = String(row.dst_url || '');
      if (dst && dst !== url && isSameHost(dst)) out.push({ title: '', url: dst });
      if (out.length >= limit) break;
    }
  } catch { /* page_links may be empty */ }

  if (out.length < limit) {
    try {
      const res = await db.execute({
        sql: `SELECT title, url FROM knowledge_items
              WHERE url != ? AND (LOWER(url) LIKE ? OR LOWER(url) LIKE ?)
              ORDER BY overall_rank DESC LIMIT ?`,
        args: [url, `https://${host}/%`, `https://%.${host}/%`, limit],
      });
      const seen = new Set(out.map(o => o.url));
      for (const row of res.rows) {
        const u = String(row.url || '');
        if (!u || seen.has(u) || !isSameHost(u)) continue;
        out.push({ title: String(row.title || ''), url: u });
        if (out.length >= limit) break;
      }
    } catch { /* table missing */ }
  }

  return out.slice(0, limit);
}

export async function suggestQueries(prefix: string, limit = 8): Promise<{ title: string; url: string }[]> {
  const q = prefix.trim().toLowerCase();
  if (q.length < 2) return [];

  const db = getTurso();
  const cleaned = q.replace(/["*()^:{}[\]]/g, '').trim();
  if (!cleaned) return [];

  // Prefix match on titles first (cheap, fast)
  try {
    const ftsPrefix = cleaned.split(/\s+/).map(w => `title:"${w}"*`).join(' AND ');
    const res = await db.execute({
      sql: `SELECT ki.title, ki.url
            FROM knowledge_fts fts
            JOIN knowledge_items ki ON ki.id = fts.rowid
            WHERE knowledge_fts MATCH ?
            LIMIT ?`,
      args: [ftsPrefix, limit],
    });
    if (res.rows.length) {
      return res.rows.map((r: any) => ({ title: r.title, url: r.url }));
    }
  } catch { /* fall through */ }

  try {
    const res = await db.execute({
      sql: `SELECT title, url FROM knowledge_items
            WHERE LOWER(title) LIKE ? ORDER BY overall_rank DESC LIMIT ?`,
      args: [`${cleaned}%`, limit],
    });
    return res.rows.map((r: any) => ({ title: r.title, url: r.url }));
  } catch {
    return [];
  }
}

export async function getStats() {
  const db = getTurso();

  const total = (await db.execute('SELECT COUNT(*) as c FROM knowledge_items')).rows[0]?.c || 0;

  const cats = (await db.execute(
    'SELECT source_category, COUNT(*) as c FROM knowledge_items GROUP BY source_category ORDER BY c DESC'
  )).rows;

  const srcs = (await db.execute(
    'SELECT source_name, COUNT(*) as c, AVG(quality_score) as q FROM knowledge_items GROUP BY source_name ORDER BY c DESC LIMIT 20'
  )).rows;

  return {
    total_items: total,
    categories: Object.fromEntries(cats.map((r: any) => [r.source_category, r.c])),
    top_sources: srcs.map((r: any) => ({
      name: r.source_name,
      count: r.c,
      avg_quality: Math.round((r.q || 0) * 100) / 100,
    })),
  };
}

// ============================================================
// PPMI THESAURUS
// ============================================================

export async function getAssociations(term: string, limit = 8): Promise<string[]> {
  const db = getTurso();
  try {
    const res = await db.execute({
      sql: `SELECT related FROM term_associations
            WHERE term = ? ORDER BY ppmi DESC, cooc DESC LIMIT ?`,
      args: [term.toLowerCase(), limit],
    });
    return res.rows.map((r: any) => String(r.related));
  } catch {
    return [];
  }
}

// ============================================================
// QUERY STATS (aggregated, no identifiers)
// ============================================================

export async function logQuery(query: string): Promise<void> {
  const normalized = query.toLowerCase().replace(/\s+/g, ' ').trim().slice(0, 200);
  if (normalized.length < 2) return;

  const db = getTurso();
  try {
    await db.execute({
      sql: `INSERT INTO query_stats (query, count, last_seen)
            VALUES (?, 1, datetime('now'))
            ON CONFLICT(query) DO UPDATE SET
              count = count + 1,
              last_seen = datetime('now')`,
      args: [normalized],
    });
  } catch {
    /* best-effort logging */
  }
}

export async function getPopularQueries(limit = 5, prefix = ''): Promise<string[]> {
  const db = getTurso();
  try {
    const res = prefix
      ? await db.execute({
          sql: `SELECT query FROM query_stats WHERE query LIKE ?
                ORDER BY count DESC, last_seen DESC LIMIT ?`,
          args: [`${prefix.toLowerCase()}%`, limit],
        })
      : await db.execute({
          sql: 'SELECT query FROM query_stats ORDER BY count DESC, last_seen DESC LIMIT ?',
          args: [limit],
        });
    return res.rows.map((r: any) => String(r.query));
  } catch {
    return [];
  }
}
