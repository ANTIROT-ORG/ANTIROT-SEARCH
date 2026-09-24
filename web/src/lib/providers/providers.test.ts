import { describe, it, expect } from 'vitest';
import { mapHnHit } from './hn';
import { mapSeItem } from './stack';
import { mapWikiHit } from './wikipedia';
import { titleFromCdxUrl, mapCdxRow } from './ccindex';

describe('Hacker News mapper', () => {
  it('maps a story to a news result', () => {
    const r = mapHnHit({ objectID: '1', title: 'SQLite release', url: 'https://x.dev/1', points: 120, num_comments: 40, author: 'pg', created_at: '2026-09-01T10:00:00Z' });
    expect(r).not.toBeNull();
    expect(r!.provider).toBe('hn');
    expect(r!.result_type).toBe('news');
    expect(r!.url).toBe('https://x.dev/1');
    expect(r!.snippet).toContain('120 pts');
  });

  it('falls back to the HN thread URL and drops empty titles', () => {
    const r = mapHnHit({ objectID: '2', title: 'No link story' });
    expect(r!.url).toBe('https://news.ycombinator.com/item?id=2');
    expect(mapHnHit({ objectID: '3' })).toBeNull();
  });
});

describe('Stack Exchange mapper', () => {
  it('maps an answered question', () => {
    const r = mapSeItem({ question_id: 9, title: 'How to use FTS5?', score: 5, is_answered: true, answer_count: 3, tags: ['sqlite', 'fts5'], creation_date: 1700000000, owner: { display_name: 'Ned' } });
    expect(r!.provider).toBe('stack');
    expect(r!.result_type).toBe('code');
    expect(r!.snippet).toContain('accepted');
    expect(r!.snippet).toContain('fts5');
    expect(r!.author).toBe('Ned');
    expect(r!.published_date).toBeTruthy();
  });

  it('drops untitled items', () => {
    expect(mapSeItem({ question_id: 1 })).toBeNull();
  });
});

describe('Wikipedia mapper', () => {
  it('maps a search hit with a clean URL and snippet', () => {
    const r = mapWikiHit({ pageid: 42, title: 'Search engine', snippet: 'A <span class="searchmatch">search</span> engine finds data.' });
    expect(r!.url).toContain('/wiki/Search_engine');
    expect(r!.snippet).not.toContain('<');
    expect(r!.provider).toBe('wikipedia');
  });
});

describe('Common Crawl CDX mapper', () => {
  it('builds a readable title from a URL path', () => {
    expect(titleFromCdxUrl('https://example.com/docs/api/intro.html')).toContain('api');
    expect(titleFromCdxUrl('https://example.com/')).toContain('example.com');
  });

  it('maps a CDX row with an ISO date', () => {
    const r = mapCdxRow({ url: 'https://example.com/docs/api-intro', timestamp: '20260712021812' }, 'CC-MAIN-2026-39');
    expect(r!.provider).toBe('ccindex');
    expect(r!.published_date).toBe('2026-07-12');
    expect(r!.snippet).toContain('Common Crawl');
    expect(mapCdxRow({}, 'x')).toBeNull();
  });
});

import { buildReverseImageUrl } from './yandex';

describe('Reverse Image Search URL generator', () => {
  it('generates valid Yandex and Google Lens reverse image lookup URLs', () => {
    const testImg = 'https://example.com/photo.jpg';
    const yandexUrl = buildReverseImageUrl(testImg, 'yandex');
    const googleUrl = buildReverseImageUrl(testImg, 'google');

    expect(yandexUrl).toContain('yandex.com/images/search?rpt=imageview&url=');
    expect(yandexUrl).toContain(encodeURIComponent(testImg));
    expect(googleUrl).toContain('lens.google.com/uploadbyurl?url=');
    expect(googleUrl).toContain(encodeURIComponent(testImg));
  });
});