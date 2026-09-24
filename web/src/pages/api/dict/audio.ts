import type { APIRoute } from 'astro';

/**
 * Pronunciation audio proxy (the DuckDuckGo `/audio/?u=` pattern):
 * streams a pronunciation file from an allowlisted host with a one-year
 * cache so playback never re-hits upstream.
 */

const ALLOWED_SUFFIXES = [
  'dictionaryapi.com',
  'merriam-webster.com',
  'upload.wikimedia.org',
  'commons.wikimedia.org',
];

const MAX_BYTES = 2 * 1024 * 1024;
const FETCH_TIMEOUT_MS = 5000;

/** Exported for unit tests: https-only, allowlisted host, no userinfo tricks. */
export function audioHostAllowed(rawUrl: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return false;
  }
  if (parsed.protocol !== 'https:') return false;
  if (parsed.username || parsed.password) return false;
  const host = parsed.hostname.toLowerCase();
  return ALLOWED_SUFFIXES.some(s => host === s || host.endsWith(`.${s}`));
}

export const GET: APIRoute = async ({ url }) => {
  const target = url.searchParams.get('u') || '';
  const bad = (status: number, message: string) =>
    new Response(JSON.stringify({ error: message }), {
      status,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
    });

  if (!target) return bad(404, 'Missing audio URL');
  if (!audioHostAllowed(target)) return bad(400, 'Host not allowed');

  try {
    const upstream = await fetch(target, {
      redirect: 'follow',
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!upstream.ok) return bad(502, 'Upstream fetch failed');

    // A redirect can leave the allowlist — re-validate the final host
    if (upstream.url && !audioHostAllowed(upstream.url)) {
      return bad(400, 'Redirect target not allowed');
    }

    const declared = Number(upstream.headers.get('content-length') || '0');
    if (declared > MAX_BYTES) return bad(413, 'Audio too large');

    const buf = await upstream.arrayBuffer();
    if (buf.byteLength > MAX_BYTES) return bad(413, 'Audio too large');

    const contentType = upstream.headers.get('content-type') || 'application/octet-stream';
    return new Response(buf, {
      headers: {
        'Content-Type': contentType,
        'Content-Length': String(buf.byteLength),
        'Cache-Control': 'public, max-age=31536000, immutable',
        'X-Content-Type-Options': 'nosniff',
      },
    });
  } catch {
    return bad(504, 'Upstream timeout');
  }
};
