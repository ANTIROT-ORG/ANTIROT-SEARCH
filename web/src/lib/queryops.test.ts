import { describe, it, expect } from 'vitest';
import {
  parseQueryOps, opsChips, removeOpsChip,
  matchesSite, matchesFiletype, matchesDates, whenLowerBound, VALID_WHEN,
} from './queryops';

describe('parseQueryOps', () => {
  it('leaves plain queries inactive', () => {
    const ops = parseQueryOps('machine learning');
    expect(ops.active).toBe(false);
    expect(ops.terms).toEqual(['machine', 'learning']);
    expect(ops.cleaned).toBe('machine learning');
  });

  it('extracts forced phrases', () => {
    const ops = parseQueryOps('"exact phrase" plus words');
    expect(ops.active).toBe(true);
    expect(ops.phrases).toEqual(['exact phrase']);
    expect(ops.terms).toEqual(['plus', 'words']);
    expect(ops.cleaned).toBe('exact phrase plus words');
    expect(ops.cleaned).not.toContain('"');
  });

  it('extracts exclusions (bare and quoted)', () => {
    const ops = parseQueryOps('python -recipe -"multi word" tutorial');
    expect(ops.exclude).toEqual(['recipe', 'multi word']);
    expect(ops.terms).toEqual(['python', 'tutorial']);
    expect(ops.active).toBe(true);
  });

  it('normalizes site: (www strip, trailing slash)', () => {
    expect(parseQueryOps('site:www.Example.com/').site).toBe('example.com');
    expect(parseQueryOps('site:arxiv.org paper').site).toBe('arxiv.org');
  });

  it('normalizes filetype: (leading dot)', () => {
    expect(parseQueryOps('filetype:.PDF report').filetype).toBe('pdf');
    expect(parseQueryOps('report filetype:pdf').filetype).toBe('pdf');
  });

  it('parses after:/before: dates plus shorthand', () => {
    const ops = parseQueryOps('climate after:2024-01-01 before:2024-06-30');
    expect(ops.dateFrom).toBe('2024-01-01');
    expect(ops.dateTo).toBe('2024-06-30');

    expect(parseQueryOps('x after:2023').dateFrom).toBe('2023-01-01');
    expect(parseQueryOps('x before:2023-05').dateTo).toBe('2023-05-31');
  });

  it('treats malformed dates as plain terms', () => {
    const ops = parseQueryOps('after:someday query');
    expect(ops.dateFrom).toBe('');
    expect(ops.active).toBe(false);
    expect(ops.terms.join(' ')).toContain('after:someday');
  });

  it('operator-only queries are active with empty cleaned text', () => {
    const ops = parseQueryOps('site:en.wikipedia.org');
    expect(ops.active).toBe(true);
    expect(ops.cleaned).toBe('');
    expect(ops.terms).toEqual([]);
  });
});

describe('opsChips / removeOpsChip', () => {
  it('round-trips chip removal back to a clean query', () => {
    const raw = 'quantum mechanics site:arxiv.org -experiment "wave function" after:2020-01-01';
    const chips = opsChips(parseQueryOps(raw));
    expect(chips.map(c => c.kind).sort()).toEqual(
      ['after', 'exclude', 'phrase', 'site'].sort()
    );

    // removing site must drop only the site operator
    const siteChip = chips.find(c => c.kind === 'site')!;
    const next = removeOpsChip(raw, siteChip);
    expect(next).not.toContain('site:arxiv.org');
    expect(next).toContain('quantum mechanics');
    expect(next).toContain('-experiment');
    expect(parseQueryOps(next).site).toBe('');
  });
});

describe('row filters', () => {
  it('matchesSite accepts exact host and subdomains only', () => {
    expect(matchesSite('https://arxiv.org/abs/1', 'arxiv.org')).toBe(true);
    expect(matchesSite('https://www.arxiv.org/x', 'arxiv.org')).toBe(true);
    expect(matchesSite('https://export.arxiv.org/x', 'arxiv.org')).toBe(true);
    expect(matchesSite('https://notarxiv.org/x', 'arxiv.org')).toBe(false);
    expect(matchesSite('https://doi.org/10.1', 'arxiv.org')).toBe(false);
    expect(matchesSite('garbage', 'arxiv.org')).toBe(false);
  });

  it('matchesFiletype checks the URL path suffix', () => {
    expect(matchesFiletype('https://x.edu/paper.pdf', 'pdf')).toBe(true);
    expect(matchesFiletype('https://x.edu/paper.pdf?download=1', 'pdf')).toBe(true);
    expect(matchesFiletype('https://x.edu/paper.html', 'pdf')).toBe(false);
    expect(matchesFiletype('anything', '')).toBe(true);
  });

  it('matchesDates enforces bounds and rejects undated rows', () => {
    expect(matchesDates('2024-06-01', '2024-01-01', '')).toBe(true);
    expect(matchesDates('2023-12-31', '2024-01-01', '')).toBe(false);
    expect(matchesDates('2024-06-01', '', '2024-01-01')).toBe(false);
    expect(matchesDates('', '2024-01-01', '')).toBe(false);
    expect(matchesDates('', '', '')).toBe(true);
  });

  it('whenLowerBound maps quick filters to ISO dates', () => {
    expect(whenLowerBound('any')).toBe('');
    expect(whenLowerBound('nope')).toBe('');
    const now = Date.parse('2026-01-31T00:00:00Z');
    expect(whenLowerBound('week', now)).toBe('2026-01-24');
    expect(whenLowerBound('day', now)).toBe('2026-01-30');
    expect(VALID_WHEN.has('year')).toBe(true);
    expect(VALID_WHEN.has('decade')).toBe(false);
  });
});
