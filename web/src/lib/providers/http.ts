/**
 * Small HTTP helper for federated providers.
 * Server-side only — never imported by client bundles.
 */

const USER_AGENT = 'Antirot/2.0 (+https://github.com/BoringRats/ratcrowler)';

export interface FetchJsonOptions {
  headers?: Record<string, string>;
  timeoutMs?: number;
}

export async function fetchJson<T>(url: string, opts: FetchJsonOptions = {}): Promise<T | null> {
  const text = await fetchText(url, opts);
  if (text == null) return null;
  try {
    return JSON.parse(text) as T;
  } catch {
    return null; // non-JSON responses (e.g. provider rate-limit notices)
  }
}

export async function fetchText(url: string, opts: FetchJsonOptions = {}): Promise<string | null> {
  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': USER_AGENT,
        'Accept': 'application/json, application/xml, text/xml, */*',
        ...(opts.headers || {}),
      },
      signal: AbortSignal.timeout(opts.timeoutMs ?? 5000),
    });
    if (!res.ok) return null;
    return await res.text();
  } catch {
    return null;
  }
}

export function stripHtml(value: string): string {
  return (value || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function truncate(value: string, max = 300): string {
  const v = (value || '').trim();
  return v.length > max ? v.slice(0, max).replace(/\s+\S*$/, '') + '...' : v;
}

export function env(key: string): string {
  const meta = (import.meta as any).env || {};
  return meta[key] || (typeof process !== 'undefined' ? (process.env?.[key] || '') : '');
}
