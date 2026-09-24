import { fetchJson } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * OpenStreetMap (OSM) Places & Geocoding Search via Nominatim API.
 * Free, open, community-driven geospatial search.
 */

interface NominatimPlace {
  place_id: number;
  osm_type: string;
  osm_id: number;
  lat: string;
  lon: string;
  display_name: string;
  class: string;
  type: string;
  importance: number;
  icon?: string;
  address?: Record<string, string>;
  boundingbox?: string[];
}

export async function searchOpenStreetMap(q: string, limit = 10): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(q)}&format=json&addressdetails=1&limit=${limit}`;

  const data = await cached(`osm:${q.toLowerCase()}:${limit}`, 86400, () =>
    fetchJson<NominatimPlace[]>(url, {
      timeoutMs: 5000,
      headers: {
        'User-Agent': 'Antirot/2.0 (https://github.com/BoringRats/ratcrowler)',
      },
    })
  );

  if (!data || !Array.isArray(data)) return [];

  return data.map(place => {
    const lat = parseFloat(place.lat);
    const lon = parseFloat(place.lon);
    const osmUrl = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=15/${lat}/${lon}`;
    
    // Build descriptive details
    const placeType = place.type.replace(/_/g, ' ');
    const placeClass = place.class.replace(/_/g, ' ');
    const city = place.address?.city || place.address?.town || place.address?.village || place.address?.county || '';
    const country = place.address?.country || '';
    const locParts = [city, country].filter(Boolean).join(', ');

    return {
      id: `osm-${place.place_id}`,
      title: place.display_name.split(',')[0] || place.display_name,
      url: osmUrl,
      snippet: `${placeType.toUpperCase()} in ${locParts || place.display_name}. Coordinates: ${lat.toFixed(4)}, ${lon.toFixed(4)}`,
      provider: 'osm',
      result_type: 'place' as const,
      source_name: 'OpenStreetMap',
      coordinates: { lat, lon },
      attributes: [
        { label: 'Type', value: `${placeClass} (${placeType})` },
        { label: 'Coordinates', value: `${lat.toFixed(4)}, ${lon.toFixed(4)}` },
        { label: 'Location', value: locParts || place.display_name },
      ],
      // OpenStreetMap map thumbnail link
      thumbnail: `https://static-maps.yandex.ru/1.x/?ll=${lon},${lat}&z=14&l=map&size=400,200&pt=${lon},${lat},pm2rdm`,
    };
  });
}
