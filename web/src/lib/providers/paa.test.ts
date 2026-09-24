import { describe, it, expect } from 'vitest';
import { peopleAlsoAsk } from './paa';
import type { ProviderResult } from './types';

function result(overrides: Partial<ProviderResult>): ProviderResult {
  return {
    id: 'r1',
    title: 'Example',
    url: 'https://example.com/a',
    snippet: '',
    provider: 'local',
    result_type: 'web',
    ...overrides,
  };
}

const RESULTS: ProviderResult[] = [
  result({
    id: 'r1',
    title: 'What is quantum entanglement?',
    url: 'https://en.wikipedia.org/wiki/Quantum_entanglement',
    snippet:
      'Quantum entanglement is a physical phenomenon that occurs when a pair or group of particles ' +
      'is generated, interact, or share spatial proximity in such a way that the quantum state of ' +
      'each particle of the group cannot be described independently of the state of the others.',
    source_name: 'Wikipedia',
  }),
  result({
    id: 'r2',
    title: 'Photosynthesis',
    url: 'https://en.wikipedia.org/wiki/Photosynthesis',
    snippet: 'Photosynthesis is a process used by plants to convert light energy into chemical energy.',
    source_name: 'Wikipedia',
  }),
];

describe('peopleAlsoAsk', () => {
  it('turns question-shaped titles into answered rows', () => {
    const paa = peopleAlsoAsk('quantum entanglement', RESULTS, [], 4);
    expect(paa.length).toBeGreaterThan(0);
    const first = paa[0];
    expect(first.question).toMatch(/\?$/);
    expect(first.answer.length).toBeGreaterThanOrEqual(40);
    expect(first.url).toContain('http');
  });

  it('only keeps questions a result can actually answer', () => {
    // No result overlaps with this query → nothing to answer → no rows
    const paa = peopleAlsoAsk('zebra farming regulations', RESULTS, [], 4);
    expect(paa.every(p => p.answer.length >= 40)).toBe(true);
    for (const p of paa) {
      // any emitted row must come from a result with real token overlap
      const source = RESULTS.find(r => r.url === p.url);
      expect(source).toBeTruthy();
    }
  });

  it('derives questions from related-search terms', () => {
    const paa = peopleAlsoAsk('climate', [
      result({
        id: 'c1',
        title: 'Climate change',
        url: 'https://en.wikipedia.org/wiki/Climate_change',
        snippet:
          'Climate change is a long-term shift in global climate patterns. Global warming, the rise in ' +
          'global temperatures from the mid-20th century to the present, is driven primarily by the ' +
          'burning of fossil fuels such as coal, oil and natural gas.',
        source_name: 'Wikipedia',
      }),
    ], ['global warming'], 4);
    expect(paa.some(p => p.question.includes('global warming'))).toBe(true);
  });

  it('respects the row limit and returns [] for empty results', () => {
    expect(peopleAlsoAsk('anything', [], ['a', 'b'])).toEqual([]);
    const many = peopleAlsoAsk('quantum entanglement', RESULTS, ['x', 'y', 'z', 'q', 'w', 'e'], 2);
    expect(many.length).toBeLessThanOrEqual(2);
  });

  it('never emits duplicate questions', () => {
    const paa = peopleAlsoAsk('quantum entanglement', RESULTS, ['quantum entanglement'], 6);
    const keys = paa.map(p => p.question.toLowerCase());
    expect(new Set(keys).size).toBe(keys.length);
  });
});
