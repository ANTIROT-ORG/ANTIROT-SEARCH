import { keywordSearch, getAssociations } from '../turso';
import { cached } from './cache';

/**
 * Related searches — term/bigram extraction from the titles of the
 * top local results. Classical co-occurrence, no ML.
 */

const STOPWORDS = new Set([
  'the','and','for','with','from','that','this','these','those','into','about',
  'over','under','between','after','before','their','there','here','when','where',
  'what','which','while','have','has','had','was','were','are','not','but','his',
  'her','its','our','your','they','them','you','who','whom','why','how','all',
  'any','can','may','might','will','would','could','should','list','overview',
  'introduction','history','types','uses','example','examples','guide','part',
  'main','other','more','most','some','such','also','only','first','second',
  'new','old','big','small','high','low','united','states','world',
]);

export async function relatedSearches(q: string, limit = 6): Promise<string[]> {
  const query = q.trim();
  if (!query) return [];

  const cachedValue = await cached(`related:v3:${query.toLowerCase()}:${limit}`, 1800, async () => {
    const results = await keywordSearch(query, undefined, 20);

    const queryLower = query.toLowerCase();
    const tokensLower = queryLower.split(/\s+/).filter(t => t.length > 2);

    // PPMI thesaurus associations first (corpus co-occurrence)
    const assocLists = await Promise.all(tokensLower.map(t => getAssociations(t, 5)));
    const thesaurus: string[] = [];
    for (const list of assocLists) {
      for (const term of list) {
        if (!queryLower.includes(term) && !thesaurus.includes(term)) thesaurus.push(term);
      }
    }

    if (!results.length) return thesaurus.slice(0, limit);

    const scores = new Map<string, number>();
    const counts = new Map<string, number>();

    results.forEach((r, index) => {
      const weight = 1 / (1 + index * 0.4);
      const tokens = (r.title || '')
        .toLowerCase()
        .split(/[^a-z0-9]+/)
        .filter(t => t.length > 3 && !STOPWORDS.has(t));

      for (const token of tokens) {
        scores.set(token, (scores.get(token) || 0) + weight);
        counts.set(token, (counts.get(token) || 0) + 1);
      }
      for (let i = 0; i < tokens.length - 1; i++) {
        const bigram = `${tokens[i]} ${tokens[i + 1]}`;
        scores.set(bigram, (scores.get(bigram) || 0) + weight * 1.5);
        counts.set(bigram, (counts.get(bigram) || 0) + 1);
      }
    });

    const titleTerms = [...scores.entries()]
      .filter(([term]) =>
        !queryLower.includes(term) &&
        term.split(' ').every(part => !queryLower.includes(part))
      )
      // Keep recurring terms or bigrams — singles from one noisy result are dropped
      .filter(([term]) => term.includes(' ') || (counts.get(term) || 0) >= 2)
      .sort((a, b) => b[1] - a[1])
      .map(([term]) => term);

    const merged = [...thesaurus, ...titleTerms.filter(t => !thesaurus.includes(t))];
    return merged.slice(0, limit);
  });

  return cachedValue || [];
}
