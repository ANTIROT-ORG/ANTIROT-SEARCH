import { fetchText, stripHtml, truncate, env } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Yandex Web & Image Search Integration + Reverse Image Search Helper.
 * Strictly indexes image URLs and metadata — never downloads raw image binaries.
 */

export function buildReverseImageUrl(imageUrl: string, engine: 'yandex' | 'google' = 'yandex'): string {
  if (engine === 'google') {
    return `https://lens.google.com/uploadbyurl?url=${encodeURIComponent(imageUrl)}`;
  }
  return `https://yandex.com/images/search?rpt=imageview&url=${encodeURIComponent(imageUrl)}`;
}

export async function searchYandexWeb(q: string, limit = 10): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const apiKey = env('YANDEX_API_KEY');
  const user = env('YANDEX_USER');

  // If Yandex XML API credentials are provided, use official API
  if (apiKey && user) {
    const url = `https://yandex.com/search/xml?user=${encodeURIComponent(user)}&key=${encodeURIComponent(apiKey)}&query=${encodeURIComponent(q)}&page=0&grouping=attr%3Dd.mode%3Dflat.groups-on-page%3D${limit}`;
    const xmlData = await cached(`yandex:web:${q.toLowerCase()}:${limit}`, 1800, async () => {
      try {
        const xml = await fetchText(url, { timeoutMs: 5000 });
        if (!xml) return [];
        const results: ProviderResult[] = [];
        // Regex parse standard Yandex XML doc entries
        const docMatches = xml.match(/<doc[\s\S]*?<\/doc>/g) || [];
        for (const doc of docMatches) {
          const urlMatch = doc.match(/<url>(.*?)<\/url>/);
          const titleMatch = doc.match(/<title>([\s\S]*?)<\/title>/);
          const passMatch = doc.match(/<passage>([\s\S]*?)<\/passage>/) || doc.match(/<headline>([\s\S]*?)<\/headline>/);

          if (urlMatch && titleMatch) {
            results.push({
              id: `yandex-${Buffer.from(urlMatch[1]).toString('base64').slice(0, 16)}`,
              title: stripHtml(titleMatch[1]),
              url: urlMatch[1],
              snippet: truncate(stripHtml(passMatch ? passMatch[1] : ''), 250),
              provider: 'yandex',
              result_type: 'web' as const,
              source_name: 'Yandex Web',
            });
          }
        }
        return results;
      } catch {
        return [];
      }
    });
    return xmlData || [];
  }

  // Keyless Fallback: Query Yandex web search endpoint with mobile UA for fast lightweight JSON/HTML
  const pubData = await cached(`yandex:web:public:${q.toLowerCase()}:${limit}`, 1800, async () => {
    try {
      const searchUrl = `https://yandex.com/search/?text=${encodeURIComponent(q)}`;
      const html = await fetchText(searchUrl, {
        timeoutMs: 4000,
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
          'Accept-Language': 'en-US,en;q=0.9',
        },
      });
      if (!html) return [];

      const results: ProviderResult[] = [];
      // Extract serp-item blocks
      const linkRegex = /<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a><\/h2>/g;
      let match;
      let count = 0;
      while ((match = linkRegex.exec(html)) !== null && count < limit) {
        const rawUrl = match[1];
        if (rawUrl.startsWith('http') && !rawUrl.includes('yandex.')) {
          results.push({
            id: `yandex-web-${count++}`,
            title: stripHtml(match[2]),
            url: rawUrl,
            snippet: `Search result from Yandex for "${q}"`,
            provider: 'yandex',
            result_type: 'web' as const,
            source_name: 'Yandex Web',
          });
        }
      }
      return results;
    } catch {
      return [];
    }
  });
  return pubData || [];
}

export async function searchYandexImages(q: string, limit = 20): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const imgData = await cached(`yandex:images:${q.toLowerCase()}:${limit}`, 3600, async () => {
    try {
      const searchUrl = `https://yandex.com/images/search?text=${encodeURIComponent(q)}`;
      const html = await fetchText(searchUrl, {
        timeoutMs: 5000,
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
          'Accept-Language': 'en-US,en;q=0.9',
        },
      });
      if (!html) return [];

      const results: ProviderResult[] = [];
      // Yandex image serp embeds JSON data in class="serp-item" data-bem attributes
      const bemRegex = /data-bem='([^']+)'/g;
      let match;
      let count = 0;

      while ((match = bemRegex.exec(html)) !== null && count < limit) {
        try {
          const parsed = JSON.parse(match[1]);
          const item = parsed?.['serp-item'];
          if (item?.dups && item.dups[0]?.url) {
            const imgUrl = item.dups[0].url;
            const thumbUrl = item.thumb?.url ? `https:${item.thumb.url}` : imgUrl;
            const title = item.snippet?.title || q;
            const domain = item.snippet?.domain || 'Yandex Images';

            results.push({
              id: `yandex-img-${count++}`,
              title: stripHtml(title),
              url: item.snippet?.url || imgUrl,
              snippet: item.snippet?.text || `Image from ${domain}`,
              provider: 'yandex_images',
              result_type: 'images' as const,
              source_name: domain,
              thumbnail: thumbUrl,
              image_url: imgUrl,
              attributes: [
                { label: 'Source', value: domain },
                { label: 'Direct Image', value: 'View Full Image', url: imgUrl },
              ],
            });
          }
        } catch {
          // Skip malformed item
        }
      }

      // Fallback: extract img tags directly if BEM data layout changed
      if (results.length === 0) {
        const imgTagRegex = /<img[^>]+src="(\/\/avatars\.mds\.yandex\.net\/[^"]+)"[^>]*alt="([^"]*)"/g;
        while ((match = imgTagRegex.exec(html)) !== null && count < limit) {
          const thumb = `https:${match[1]}`;
          results.push({
            id: `yandex-img-fb-${count++}`,
            title: stripHtml(match[2] || q),
            url: thumb,
            snippet: `Image result for "${q}"`,
            provider: 'yandex_images',
            result_type: 'images' as const,
            source_name: 'Yandex Images',
            thumbnail: thumb,
            image_url: thumb,
          });
        }
      }

      return results;
    } catch {
      return [];
    }
  });
  return imgData || [];
}
