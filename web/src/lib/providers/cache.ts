import { getCached, setCache } from '../redis';

/**
 * Two-tier cache for provider responses:
 *   1. Redis when REDIS_URL is configured
 *   2. In-process TTL map fallback (per server instance)
 *
 * Failed lookups are cached briefly (max 60s) so provider rate limits
 * and outages don't hammer the upstream per request.
 */

interface MemEntry {
  exp: number;
  val: any;
}

const MEM = new Map<string, MemEntry>();
const MEM_MAX = 500;

export async function cached<T>(
  key: string,
  ttlSeconds: number,
  fn: () => Promise<T | null>
): Promise<T | null> {
  const fullKey = `prov:${key}`;

  const fromRedis = await getCached<T>(fullKey);
  if (fromRedis != null) return fromRedis;

  const mem = MEM.get(fullKey);
  if (mem && mem.exp > Date.now()) return mem.val as T;

  let value: T | null = null;
  try {
    value = await fn();
  } catch {
    value = null;
  }

  const ttl = value == null ? Math.min(ttlSeconds, 60) : ttlSeconds;
  MEM.set(fullKey, { exp: Date.now() + ttl * 1000, val: value });
  if (MEM.size > MEM_MAX) {
    const oldest = MEM.keys().next().value;
    if (oldest) MEM.delete(oldest);
  }
  await setCache(fullKey, value, ttl);

  return value;
}
