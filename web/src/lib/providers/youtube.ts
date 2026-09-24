import { fetchJson, stripHtml, truncate, env } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * YouTube Data API v3 — requires YOUTUBE_API_KEY in web/.env.
 * Search costs 100 quota units, so responses are cached aggressively.
 */

interface YouTubeResponse {
  items?: {
    id?: { videoId?: string };
    snippet?: {
      title?: string;
      description?: string;
      channelTitle?: string;
      publishedAt?: string;
      thumbnails?: Record<string, { url?: string }>;
    };
  }[];
}

export function youtubeConfigured(): boolean {
  return !!env('YOUTUBE_API_KEY');
}

export async function searchYouTube(q: string, limit = 15, page = 1): Promise<ProviderResult[]> {
  const key = env('YOUTUBE_API_KEY');
  if (!key || !q.trim() || page > 1) return [];

  const url = 'https://www.googleapis.com/youtube/v3/search' +
    `?part=snippet&type=video&maxResults=${Math.min(limit, 25)}` +
    `&q=${encodeURIComponent(q)}&key=${key}`;

  const data = await cached(`youtube:${q.toLowerCase()}:${limit}`, 1800, () =>
    fetchJson<YouTubeResponse>(url, { timeoutMs: 6000 })
  );

  return (data?.items || [])
    .filter(it => it.id?.videoId)
    .map(it => ({
      id: `youtube-${it.id!.videoId}`,
      title: stripHtml(it.snippet?.title || ''),
      url: `https://www.youtube.com/watch?v=${it.id!.videoId}`,
      snippet: truncate(stripHtml(it.snippet?.description || ''), 200),
      provider: 'youtube',
      result_type: 'videos' as const,
      source_name: 'YouTube',
      author: stripHtml(it.snippet?.channelTitle || ''),
      published_date: it.snippet?.publishedAt || '',
      thumbnail: it.snippet?.thumbnails?.medium?.url || it.snippet?.thumbnails?.default?.url || '',
    }));
}
