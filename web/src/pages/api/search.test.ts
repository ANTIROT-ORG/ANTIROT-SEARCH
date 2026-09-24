import { describe, it, expect } from 'vitest';
import { buildAnswer } from './search';

const LONG = (s: string) => `${s} ${'filler words to clear the minimum snippet length threshold. '.repeat(4)}`;

describe('buildAnswer', () => {
  it('prefers a title-matched result over a higher-ranked snippet mention', () => {
    const results = [
      { title: 'Ocean', url: 'https://en.wikipedia.org/wiki/Ocean',
        snippet: LONG('The ocean supports ecosystems where photosynthesis occurs in phytoplankton.'),
        source_name: 'Wikipedia' },
      { title: 'Photosynthesis: An Overview', url: 'https://doi.org/10.1/x',
        snippet: LONG('Photosynthesis converts light energy into chemical energy in plants and algae.'),
        source_name: 'Crossref' },
    ];
    const answer = buildAnswer('photosynthesis', results);
    expect(answer).toBeTruthy();
    expect(answer!.title).toContain('Photosynthesis');
  });

  it('returns null when no result is about the query', () => {
    const results = [
      { title: 'Unrelated', url: 'https://x.edu/a',
        snippet: LONG('Nothing in this passage has anything to do with the searched topic at all.'),
        source_name: 'X' },
    ];
    expect(buildAnswer('photosynthesis', results)).toBeNull();
  });

  it('rejects snippets that mention the topic only at the end', () => {
    // No title match, and the topic never appears early → no confident answer
    const results = [
      { title: 'Marine ecosystems', url: 'https://x.edu/b',
        snippet: LONG('Waters. '),
        source_name: 'X' },
    ];
    expect(buildAnswer('photosynthesis', results)).toBeNull();
  });

  it('needs a sufficiently long snippet and at least one real token', () => {
    expect(buildAnswer('climate', [
      { title: 'Short', url: 'https://x.edu/c', snippet: 'too short', source_name: 'X' },
    ])).toBeNull();
    expect(buildAnswer('', [
      { title: 'Anything', url: 'https://x.edu/d', snippet: LONG('text'), source_name: 'X' },
    ])).toBeNull();
  });
});
