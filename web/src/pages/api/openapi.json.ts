import type { APIRoute } from 'astro';
import { openapiSpec } from '../../data/openapi';

export const GET: APIRoute = async () => {
  return new Response(JSON.stringify(openapiSpec, null, 2), {
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, s-maxage=3600',
    },
  });
};
