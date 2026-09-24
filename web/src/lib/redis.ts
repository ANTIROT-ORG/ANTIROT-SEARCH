import Redis from 'ioredis';

let _redis: Redis | null = null;
let _connecting = false;

export function getRedis(): Redis | null {
  const url = import.meta.env.REDIS_URL;
  if (!url) return null;

  if (_redis) return _redis;
  if (_connecting) return null;

  try {
    _connecting = true;
    _redis = new Redis(url, {
      maxRetriesPerRequest: 2,
      connectTimeout: 3000,
      lazyConnect: false,
      enableReadyCheck: true,
      retryStrategy(times) {
        if (times > 3) return null; // Stop retrying
        return Math.min(times * 200, 2000);
      },
    });

    _redis.on('error', () => {
      _redis = null;
      _connecting = false;
    });

    _redis.on('ready', () => {
      _connecting = false;
    });

    return _redis;
  } catch {
    _redis = null;
    _connecting = false;
    return null;
  }
}

const CACHE_TTL = 3600;
const STATS_TTL = 300;

// ── In-process fallback: instant responses when Redis isn't configured ──
const MEM_MAX = 500;
const MEM = new Map<string, { v: string; exp: number }>();

function memGet(key: string): string | null {
  const hit = MEM.get(key);
  if (!hit) return null;
  if (Date.now() > hit.exp) { MEM.delete(key); return null; }
  return hit.v;
}

function memSet(key: string, value: string, ttlSec: number): void {
  if (MEM.size >= MEM_MAX) {
    // Evict oldest ~20% (Map preserves insertion order)
    let drop = Math.ceil(MEM_MAX * 0.2);
    for (const k of MEM.keys()) {
      MEM.delete(k);
      if (--drop <= 0) break;
    }
  }
  MEM.set(key, { v: value, exp: Date.now() + ttlSec * 1000 });
}

export async function getCached<T>(key: string): Promise<T | null> {
  const redis = getRedis();
  if (!redis) {
    const data = memGet(`ratsearch:${key}`);
    return data ? (JSON.parse(data) as T) : null;
  }

  try {
    const data = await redis.get(`ratsearch:${key}`);
    if (data) return JSON.parse(data) as T;
  } catch { /* cache miss */ }
  return null;
}

export async function setCache(key: string, value: any, ttl = CACHE_TTL): Promise<void> {
  const redis = getRedis();
  if (!redis) {
    try { memSet(`ratsearch:${key}`, JSON.stringify(value), ttl); } catch { /* over quota */ }
    return;
  }

  try {
    await redis.setex(`ratsearch:${key}`, ttl, JSON.stringify(value));
  } catch { /* cache write failed */ }
}

export async function getCachedSearch(query: string, category: string | null, page: number) {
  const key = `search:${query}:${category || 'all'}:${page}`;
  return getCached<any[]>(key);
}

export async function setCachedSearch(query: string, category: string | null, page: number, results: any[]) {
  const key = `search:${query}:${category || 'all'}:${page}`;
  await setCache(key, results, 1800);
}

export async function getCachedStats() {
  return getCached<any>('stats');
}

export async function setCachedStats(stats: any) {
  await setCache('stats', stats, STATS_TTL);
}
