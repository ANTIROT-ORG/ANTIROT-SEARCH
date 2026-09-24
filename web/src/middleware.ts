import { defineMiddleware } from 'astro:middleware';

export const onRequest = defineMiddleware(async (context, next) => {
  try {
    const response = await next();
    // Add security headers to all responses
    response.headers.set('X-Content-Type-Options', 'nosniff');
    response.headers.set('X-Frame-Options', 'DENY');
    response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
    return response;
  } catch (e: any) {
    // Return JSON for API routes, HTML for pages
    const isApi = context.url.pathname.startsWith('/api/');
    if (isApi) {
      return new Response(JSON.stringify({ error: e.message || 'Internal error' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    throw e;
  }
});
