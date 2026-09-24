import type { ProviderResult } from './types';

/**
 * People also ask — deterministic question/answer extraction from the
 * result set: questions come from result titles and related-search terms,
 * answers are verbatim snippets from the best-matching result.
 * No generation, no ML — if no result can answer it, the row is dropped.
 */

export interface PaaItem {
  question: string;
  answer: string;
  url: string;
  source_name: string;
}

const QUESTION_STARTER =
  /^(what|how|why|when|where|who|which|is|are|was|were|can|could|does|do|did|should|would|difference|definition of|meaning of)\b/i;

const STOPWORDS = new Set([
  'the', 'a', 'an', 'is', 'are', 'of', 'in', 'on', 'to', 'for', 'and', 'or',
  'how', 'what', 'why', 'when', 'where', 'who', 'which', 'does', 'can', 'do',
]);

function tokens(text: string): string[] {
  return (text.toLowerCase().match(/[a-z0-9]{3,}/g) || []).filter(t => !STOPWORDS.has(t));
}

/** Score a result against a question; >0 only with real token overlap. */
function answerScore(question: string, result: ProviderResult): number {
  const qTokens = tokens(question);
  if (!qTokens.length) return 0;
  const title = new Set(tokens(result.title));
  const body = new Set(tokens(`${result.snippet || ''}`));

  let hits = 0;
  for (const t of qTokens) {
    if (title.has(t)) hits += 2;
    else if (body.has(t)) hits += 1;
  }
  return hits / (qTokens.length * 2);
}

function toQuestion(term: string): string {
  const t = term.trim().replace(/[?.]+$/, '');
  if (QUESTION_STARTER.test(t)) return /[?]$/.test(t) ? t : `${t}?`;
  return `What is ${t}?`;
}

export function peopleAlsoAsk(
  query: string,
  results: ProviderResult[],
  related: string[],
  limit = 4
): PaaItem[] {
  if (!results.length) return [];

  const candidates: string[] = [];

  // 1. Question-shaped titles from the result set
  for (const r of results) {
    const title = (r.title || '').trim();
    if (title && QUESTION_STARTER.test(title) && title.length <= 120) candidates.push(title);
  }

  // 2. Query + related terms in question form
  candidates.push(toQuestion(query));
  for (const term of related.slice(0, 6)) candidates.push(toQuestion(term));

  const out: PaaItem[] = [];
  const seenQuestions = new Set<string>();

  for (const candidate of candidates) {
    if (out.length >= limit) break;
    const question = candidate.endsWith('?') ? candidate : `${candidate}`;
    const key = question.toLowerCase();
    if (seenQuestions.has(key)) continue;
    seenQuestions.add(key);

    // Pick the best-scoring result that can answer it
    let best: ProviderResult | null = null;
    let bestScore = 0;
    for (const r of results) {
      const s = answerScore(question, r);
      if (s > bestScore) { bestScore = s; best = r; }
    }
    if (!best || bestScore < 0.5) continue;

    const answer = (best.snippet || '').trim();
    if (answer.length < 40) continue;

    out.push({
      question,
      answer: answer.length > 320 ? `${answer.slice(0, 320).replace(/\s+\S*$/, '')}…` : answer,
      url: best.url,
      source_name: best.source_name || '',
    });
  }

  return out;
}
