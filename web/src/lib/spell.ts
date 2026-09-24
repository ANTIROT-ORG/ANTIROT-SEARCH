/**
 * Did-you-mean — curated corrections + edit-distance fallback.
 * Ported from search/query_processor.py so the serving side matches
 * the Python pipeline's behaviour.
 */

export const SPELLING_CORRECTIONS: Record<string, string> = {
  'reciepe': 'recipe', 'recipie': 'recipe', 'recepie': 'recipe',
  'algorith': 'algorithm', 'algorithim': 'algorithm', 'algoritm': 'algorithm',
  'pythn': 'python', 'pyhton': 'python', 'pyton': 'python',
  'javscript': 'javascript', 'javasript': 'javascript',
  'photoshopt': 'photoshop',
  'mathmatics': 'mathematics', 'matheatics': 'mathematics',
  'phisics': 'physics', 'phsyics': 'physics', 'phyics': 'physics',
  'biolog': 'biology', 'bilogy': 'biology',
  'chmistry': 'chemistry', 'chemestry': 'chemistry',
  'progrmming': 'programming', 'programing': 'programming',
  'nutriton': 'nutrition', 'nutrtion': 'nutrition',
  'histroy': 'history', 'hsitory': 'history',
  'geoagrophy': 'geography', 'gography': 'geography',
  'cokking': 'cooking', 'cramming': 'learning', 'grammer': 'grammar',
  'enviroment': 'environment', 'enviorment': 'environment',
  'sustanable': 'sustainable', 'sustainble': 'sustainable',
  'democrcy': 'democracy', 'demoracy': 'democracy',
  'ecosytem': 'ecosystem', 'ecosystm': 'ecosystem',
  'renassiance': 'renaissance', 'renaisance': 'renaissance',
  'diabeties': 'diabetes', 'diabetus': 'diabetes',
  'antibotic': 'antibiotic', 'antibitocs': 'antibiotics',
  'philosphy': 'philosophy', 'philospher': 'philosopher',
  'psycology': 'psychology', 'psycholog': 'psychology',
  'archeology': 'archaeology', 'archaeolgy': 'archaeology',
  'linguistcs': 'linguistics', 'linguistis': 'linguistics',
  'ecnomics': 'economics',
  'thermodynaics': 'thermodynamics', 'thermodyamics': 'thermodynamics',
  'evoluton': 'evolution', 'evolutiion': 'evolution',
  'clmate change': 'climate change', 'climte change': 'climate change',
  'artifical intelligence': 'artificial intelligence',
  'machine lerning': 'machine learning', 'maching learning': 'machine learning',
  'deep lerning': 'deep learning',
  'disection': 'dissection', 'destilation': 'distillation',
  'catalist': 'catalyst', 'moleculer': 'molecular',
  'protien': 'protein', 'proten': 'protein',
  'vitiman': 'vitamin', 'vitemin': 'vitamin',
  'hormon': 'hormone', 'hormomes': 'hormones',
  'sceince': 'science', 'sciense': 'science',
  'relativty': 'relativity', 'quantum mechancis': 'quantum mechanics',
  'derrivative': 'derivative', 'derivitive': 'derivative', 'derivatve': 'derivative',
  'integrl': 'integral', 'intergral': 'integral',
  'trignometry': 'trigonometry', 'geomety': 'geometry',
  'spelelogist': 'speleologist', 'mycolgy': 'mycology',
  'entomolgy': 'entomology', 'botny': 'botany', 'botony': 'botany',
  'zology': 'zoology', 'zoolog': 'zoology',
  'astronomy': 'astronomy',
};

export function editDistance(a: string, b: string): number {
  if (a.length < b.length) return editDistance(b, a);
  if (!b.length) return a.length;

  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 0; i < a.length; i++) {
    const curr = [i + 1];
    for (let j = 0; j < b.length; j++) {
      const insert = prev[j + 1] + 1;
      const del = curr[j] + 1;
      const sub = prev[j] + (a[i] !== b[j] ? 1 : 0);
      curr.push(Math.min(insert, del, sub));
    }
    prev = curr;
  }
  return prev[b.length];
}

const VOCAB = [...new Set(Object.values(SPELLING_CORRECTIONS))];

function closestMatch(word: string): string | null {
  let best: string | null = null;
  let bestDist = Infinity;
  for (const known of VOCAB) {
    const d = editDistance(word, known);
    if (d < bestDist && d <= 2 && d < word.length * 0.4) {
      bestDist = d;
      best = known;
    }
  }
  return best;
}

/**
 * Exact curated correction for the whole query (including multi-word keys
 * like "quantum mechancis"). Safe to apply unconditionally; null if no hit.
 */
export function exactCorrection(query: string): string | null {
  const q = query.toLowerCase().replace(/\s+/g, ' ').trim();
  if (!q) return null;
  const direct = SPELLING_CORRECTIONS[q];
  if (direct && direct !== q) return direct;

  // Adjacent-word (bigram) keys: replace both words in place
  const words = q.split(' ');
  for (let i = 0; i < words.length - 1; i++) {
    const key = `${words[i]} ${words[i + 1]}`;
    const fixed = SPELLING_CORRECTIONS[key];
    if (fixed && fixed !== key) {
      const head = words.slice(0, i).join(' ');
      const tail = words.slice(i + 2).join(' ');
      return [head, fixed, tail].filter(Boolean).join(' ');
    }
  }
  return null;
}

/**
 * Suggest a corrected query, or null when every token looks right.
 * `query` should be the operator-stripped query text.
 */
export function didYouMean(query: string): string | null {
  const exact = exactCorrection(query);
  if (exact) return exact;

  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return null;

  let changed = false;
  const corrected = words.map(w => {
    const direct = SPELLING_CORRECTIONS[w];
    if (direct) { changed = true; return direct; }
    if (w.length > 3) {
      const near = closestMatch(w);
      if (near) { changed = true; return near; }
    }
    return w;
  });

  if (!changed) return null;
  const suggestion = corrected.join(' ');
  return suggestion === query.toLowerCase().trim() ? null : suggestion;
}
