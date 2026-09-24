import type { APIRoute } from 'astro';
import { getStats, ensureTables } from '../../lib/turso';
import { getCachedStats, setCachedStats } from '../../lib/redis';

export const GET: APIRoute = async () => {
  const cached = await getCachedStats();
  if (cached) {
    return new Response(JSON.stringify(cached), {
      headers: { 'Content-Type': 'application/json',
        'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=60', 'X-Cache': 'HIT' },
    });
  }

  try {
    await ensureTables();
    const stats = await getStats();
    await setCachedStats(stats);
    return new Response(JSON.stringify(stats), {
      headers: { 'Content-Type': 'application/json',
        'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=60', 'X-Cache': 'MISS' },
    });
  } catch (e: any) {
    return new Response(JSON.stringify({ error: e.message, total_items: 0 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
