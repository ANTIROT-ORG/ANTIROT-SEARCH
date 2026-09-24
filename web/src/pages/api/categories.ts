import type { APIRoute } from 'astro';

const CATEGORIES = [
  { id: 'knowledge_foundations', name: 'Knowledge Foundations' },
  { id: 'science_research', name: 'Science & Research' },
  { id: 'university_learning', name: 'University Learning' },
  { id: 'programming_engineering', name: 'Programming & Engineering' },
  { id: 'data_economics', name: 'Data & Economics' },
  { id: 'deep_thinking', name: 'Deep Thinking' },
  { id: 'tech_news', name: 'Tech News' },
  { id: 'health_medical', name: 'Health & Medical' },
];

export const GET: APIRoute = async () => {
  return new Response(JSON.stringify({ categories: CATEGORIES }), {
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, s-maxage=3600',
    },
  });
};
