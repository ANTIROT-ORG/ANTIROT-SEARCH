import type { APIRoute } from 'astro';
import { buildReverseImageUrl } from '../../lib/providers/yandex';

export const GET: APIRoute = async ({ url }) => {
  const imageUrl = url.searchParams.get('url') || '';
  const engine = (url.searchParams.get('engine') || 'yandex') as 'yandex' | 'google';

  if (!imageUrl) {
    return new Response(JSON.stringify({ error: 'Missing url parameter' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const yandexUrl = buildReverseImageUrl(imageUrl, 'yandex');
  const googleLensUrl = buildReverseImageUrl(imageUrl, 'google');

  return new Response(JSON.stringify({
    image_url: imageUrl,
    reverse_url: engine === 'google' ? googleLensUrl : yandexUrl,
    engines: {
      yandex: yandexUrl,
      google_lens: googleLensUrl,
    },
  }), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=3600',
    },
  });
};

export const POST: APIRoute = async ({ request }) => {
  try {
    const contentType = request.headers.get('content-type') || '';
    let imageUrl = '';

    if (contentType.includes('application/json')) {
      const body = await request.json();
      imageUrl = body.url || '';
    } else if (contentType.includes('multipart/form-data') || contentType.includes('application/x-www-form-urlencoded')) {
      const formData = await request.formData();
      imageUrl = (formData.get('url') as string) || '';
    }

    if (!imageUrl) {
      return new Response(JSON.stringify({ error: 'Please provide an image URL for reverse search' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const yandexUrl = buildReverseImageUrl(imageUrl, 'yandex');
    const googleLensUrl = buildReverseImageUrl(imageUrl, 'google');

    return new Response(JSON.stringify({
      success: true,
      image_url: imageUrl,
      yandex_search: yandexUrl,
      google_lens: googleLensUrl,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (e: any) {
    return new Response(JSON.stringify({ error: e?.message || 'Reverse image search failed' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
