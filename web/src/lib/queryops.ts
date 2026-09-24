/**
 * Query operators — Google-style, parsed BEFORE normalization so quotes
 * and colons survive sanitization.
 *
 * Supported:
 *   "exact phrase"      forced phrase
 *   -word  -"phrase"    exclusion
 *   site:example.com    restrict to host (and subdomains)
 *   filetype:pdf        restrict by URL suffix
 *   after:2024-01-01    published-date lower bound (also YYYY / YYYY-MM)
 *   before:2024-12-31   published-date upper bound
 */

export interface QueryOps {
  phrases: string[];
  exclude: string[];
  site: string;
  filetype: string;
  dateFrom: string;
  dateTo: string;
  terms: string[];
  cleaned: string;
  active: boolean;
}

const EMPTY: QueryOps = {
  phrases: [], exclude: [], site: '', filetype: '',
  dateFrom: '', dateTo: '', terms: [], cleaned: '', active: false,
};

function normDate(value: string, end: boolean): string {
  const v = value.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return v;
  if (/^\d{4}-\d{2}$/.test(v)) return end ? `${v}-31` : `${v}-01`;
  if (/^\d{4}$/.test(v)) return end ? `${v}-12-31` : `${v}-01-01`;
  return '';
}

export function parseQueryOps(raw: string): QueryOps {
  const ops: QueryOps = structuredClone(EMPTY);
  const rest: string[] = [];

  // Tokenize respecting quotes: each pass pulls one construct at a time
  const tokens = raw.match(/-?"[^"]*"|\S+/g) || [];

  for (const token of tokens) {
    let t = token;

    if (t.startsWith('-') && t.length > 1) {
      t = t.slice(1);
      const inner = stripQuotes(t);
      if (inner) { ops.exclude.push(inner.toLowerCase()); continue; }
    }

    const colon = t.match(/^(site|filetype|after|before):(.+)$/i);
    if (colon) {
      const key = colon[1].toLowerCase();
      const value = stripQuotes(colon[2]);
      if (!value) continue;
      if (key === 'site') {
        ops.site = value.toLowerCase().replace(/^www\./, '').replace(/\/+$/, '');
        continue;
      }
      if (key === 'filetype') {
        ops.filetype = value.toLowerCase().replace(/^\./, '');
        continue;
      }
      const d = normDate(value, key === 'before');
      if (d) {
        if (key === 'after') ops.dateFrom = d;
        else ops.dateTo = d;
        continue;
      }
      // malformed date → treat the whole token as a plain term
    }

    if (t.startsWith('"') && t.endsWith('"') && t.length >= 2) {
      const inner = t.slice(1, -1).trim();
      if (inner) { ops.phrases.push(inner); continue; }
      continue;
    }

    rest.push(t);
  }

  ops.terms = rest.join(' ').split(/\s+/).filter(Boolean);
  ops.cleaned = [...ops.phrases, ...ops.terms].join(' ').trim();
  ops.active = !!(
    ops.phrases.length || ops.exclude.length || ops.site ||
    ops.filetype || ops.dateFrom || ops.dateTo
  );
  return ops;
}

function stripQuotes(v: string): string {
  return v.replace(/^"|"$/g, '').trim();
}

export interface OpsChip {
  label: string;
  kind: 'site' | 'filetype' | 'phrase' | 'exclude' | 'after' | 'before';
  value: string;
}

/** Chips for the SERP UI — one removable token per operator. */
export function opsChips(ops: QueryOps): OpsChip[] {
  const chips: OpsChip[] = [];
  if (ops.site) chips.push({ label: `site:${ops.site}`, kind: 'site', value: ops.site });
  if (ops.filetype) chips.push({ label: `filetype:${ops.filetype}`, kind: 'filetype', value: ops.filetype });
  for (const p of ops.phrases) chips.push({ label: `"${p}"`, kind: 'phrase', value: p });
  for (const e of ops.exclude) chips.push({ label: `-${e}`, kind: 'exclude', value: e });
  if (ops.dateFrom) chips.push({ label: `after:${ops.dateFrom}`, kind: 'after', value: ops.dateFrom });
  if (ops.dateTo) chips.push({ label: `before:${ops.dateTo}`, kind: 'before', value: ops.dateTo });
  return chips;
}

/** Remove one operator from the raw query string (chip dismissal). */
export function removeOpsChip(raw: string, chip: OpsChip): string {
  const escaped = chip.value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const patterns: Record<OpsChip['kind'], string> = {
    site: `site:\\s*"?(?:www\\.)?${escaped}[^\\s]*"?`,
    filetype: `filetype:\\s*"?\\.?${escaped}"?`,
    phrase: `"${escaped}"`,
    exclude: (chip.value.match(/\s/)
      ? `-\\s*"${escaped}"`
      : `-${escaped}\\b`),
    after: `after:\\s*${escaped}`,
    before: `before:\\s*${escaped}`,
  };
  return raw.replace(new RegExp(patterns[chip.kind], 'gi'), '').replace(/\s+/g, ' ').trim();
}

/** URL suffix for filetype: — 'pdf' matches .pdf (with or without query string). */
export function matchesFiletype(url: string, filetype: string): boolean {
  if (!filetype) return true;
  try {
    const path = new URL(url).pathname.toLowerCase();
    return path.endsWith(`.${filetype}`);
  } catch {
    return url.toLowerCase().split('?')[0].endsWith(`.${filetype}`);
  }
}

export function matchesSite(url: string, site: string): boolean {
  if (!site) return true;
  try {
    const host = new URL(url).hostname.toLowerCase().replace(/^www\./, '');
    return host === site || host.endsWith(`.${site}`);
  } catch {
    return false;
  }
}

export function matchesDates(published: string, from: string, to: string): boolean {
  if (!from && !to) return true;
  if (!published) return false;
  const ts = Date.parse(published);
  if (isNaN(ts)) return false;
  if (from && published < from) return false;
  if (to && published.slice(0, 10) > to) return false;
  return true;
}

/** `when` quick filter: day|week|month|year → ISO lower bound. */
export function whenLowerBound(when: string, now = Date.now()): string {
  const days: Record<string, number> = { day: 1, week: 7, month: 31, year: 365 };
  const d = days[when];
  if (!d) return '';
  return new Date(now - d * 86_400_000).toISOString().slice(0, 10);
}

export const VALID_WHEN = new Set(['any', 'day', 'week', 'month', 'year']);
