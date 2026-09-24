import type { APIRoute } from 'astro';
import { suggestQueries, getPopularQueries, ensureTables } from '../../lib/turso';
import { getCached, setCache } from '../../lib/redis';

const MAX_PREFIX_LEN = 100;

export const GET: APIRoute = async ({ url }) => {
  const prefix = (url.searchParams.get('q') || '').trim();
  const limit = Math.min(parseInt(url.searchParams.get('limit') || '8'), 20);

  if (prefix.length < 2 || prefix.length > MAX_PREFIX_LEN) {
    return new Response(JSON.stringify({ suggestions: [] }), {
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
    });
  }

  const cacheKey = `suggest:v3:${prefix.toLowerCase()}:${limit}`;
  const cached = await getCached<{ title: string; url: string }[]>(cacheKey);
  if (cached) {
    return new Response(JSON.stringify({ suggestions: cached }), {
      headers: { 'Content-Type': 'application/json',
        'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=60', 'X-Cache': 'HIT' },
    });
  }

  await ensureTables();

  let suggestions: { title: string; url: string }[] = [];
  try {
    const [titles, popular] = await Promise.all([
      suggestQueries(prefix, limit),
      getPopularQueries(3, prefix),
    ]);

    const urlByTitle = new Map(titles.map(t => [t.title.toLowerCase(), t.url]));
    const merged = new Map<string, string>();
    for (const title of [...popular, ...titles.map(t => t.title)]) {
      const key = title.toLowerCase();
      if (!merged.has(key)) merged.set(key, title);
    }

    suggestions = [...merged.values()].slice(0, limit).map(title => ({
      title,
      url: urlByTitle.get(title.toLowerCase()) || '',
    }));
  } catch {
    suggestions = [];
  }

  await setCache(cacheKey, suggestions, 300);

  return new Response(JSON.stringify({ suggestions }), {
    headers: { 'Content-Type': 'application/json',
        'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=60', 'X-Cache': 'MISS' },
  });
};
