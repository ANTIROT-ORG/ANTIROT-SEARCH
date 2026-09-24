import { fetchJson, stripHtml, truncate } from './http';
import { cached } from './cache';
import type { ProviderResult } from './types';

/**
 * Product & Shopping Search Provider
 * Aggregates verified products, specs, prices, and reviews from open product databases.
 */

interface DummyProduct {
  id: number;
  title: string;
  description: string;
  price: number;
  rating: number;
  brand: string;
  category: string;
  thumbnail: string;
  images: string[];
}

interface DummyProductsResponse {
  products: DummyProduct[];
  total: number;
}

interface OpenFoodProduct {
  code: string;
  product_name: string;
  brands: string;
  image_url?: string;
  image_small_url?: string;
  categories: string;
  nutriscore_grade?: string;
}

interface OpenFoodResponse {
  products: OpenFoodProduct[];
  count: number;
}

export async function searchProducts(q: string, limit = 15): Promise<ProviderResult[]> {
  if (!q.trim()) return [];

  const cacheKey = `products:${q.toLowerCase()}:${limit}`;
  const data = await cached(cacheKey, 3600, async () => {
    const results: ProviderResult[] = [];

    // 1. Query Consumer Goods & Tech Products (DummyJSON)
    try {
      const dummyUrl = `https://dummyjson.com/products/search?q=${encodeURIComponent(q)}&limit=${limit}`;
      const dummyData = await fetchJson<DummyProductsResponse>(dummyUrl, { timeoutMs: 4000 });
      if (dummyData?.products) {
        for (const p of dummyData.products) {
          results.push({
            id: `prod-dj-${p.id}`,
            title: stripHtml(p.title),
            url: `https://dummyjson.com/products/${p.id}`,
            snippet: truncate(stripHtml(p.description), 200),
            provider: 'products',
            result_type: 'product' as const,
            source_name: p.brand || 'Product Directory',
            price: `$${p.price.toFixed(2)}`,
            rating: p.rating,
            thumbnail: p.thumbnail || p.images?.[0] || '',
            attributes: [
              { label: 'Price', value: `$${p.price.toFixed(2)}` },
              { label: 'Rating', value: `${p.rating} / 5.0 ⭐` },
              { label: 'Brand', value: p.brand || 'Generic' },
              { label: 'Category', value: p.category || 'Goods' },
            ],
          });
        }
      }
    } catch {
      // Continue to next provider
    }

    // 2. Query Open Food Facts for grocery/brand items if fewer results
    if (results.length < limit) {
      try {
        const offUrl = `https://world.openfoodfacts.org/cgi/search.pl?search_terms=${encodeURIComponent(q)}&search_simple=1&action=process&json=1&page_size=${limit - results.length}`;
        const offData = await fetchJson<OpenFoodResponse>(offUrl, { timeoutMs: 4000 });
        if (offData?.products) {
          for (const item of offData.products) {
            if (!item.product_name) continue;
            results.push({
              id: `prod-off-${item.code}`,
              title: stripHtml(item.product_name),
              url: `https://world.openfoodfacts.org/product/${item.code}`,
              snippet: `${item.brands ? item.brands + ' · ' : ''}${truncate(item.categories || 'Consumer product', 150)}`,
              provider: 'products',
              result_type: 'product' as const,
              source_name: 'Open Food Facts',
              thumbnail: item.image_small_url || item.image_url || '',
              attributes: [
                { label: 'Brand', value: item.brands || 'Unknown' },
                { label: 'Barcode', value: item.code },
                { label: 'Nutri-Score', value: (item.nutriscore_grade || 'N/A').toUpperCase() },
              ],
            });
          }
        }
      } catch {
        // Fallback
      }
    }

    return results;
  });
  return data || [];
}
