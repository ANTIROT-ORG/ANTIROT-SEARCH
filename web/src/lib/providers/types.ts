export type Vertical = 'all' | 'images' | 'videos' | 'news' | 'code' | 'maps' | 'academic' | 'products';

export type ResultType = 'web' | 'images' | 'videos' | 'news' | 'code' | 'research' | 'knowledge' | 'place' | 'product';

export interface ProviderResult {
  id: string;
  title: string;
  url: string;
  snippet: string;
  provider: string;
  result_type: ResultType;
  source_name?: string;
  author?: string;
  published_date?: string;
  thumbnail?: string;
  duration?: number;
  license?: string;
  attribution?: string;
  repo?: string;
  path?: string;
  language?: string;
  stars?: number;
  quality_score?: number;
  score?: number;
  media?: any[];
  engine?: string;
  sitelinks?: { title: string; url: string }[];
  entity_id?: string;
  image_url?: string;
  attributes?: { label: string; value: string; url?: string }[];
  price?: string;
  rating?: number;
  coordinates?: { lat: number; lon: number };
}

export interface ProviderStat {
  name: string;
  count: number;
  ms: number;
  error?: string;
}

export interface FederationResponse {
  results: ProviderResult[];
  knowledge_card: ProviderResult | null;
  providers: ProviderStat[];
  pagination: boolean;
  card_ms?: number;
}

export const PROVIDER_WEIGHTS: Record<string, number> = {
  local: 1.00,
  duckdb: 0.98,
  github: 0.95,
  stack: 0.90,
  wikipedia: 0.92,
  wikidata: 0.90,
  osm: 0.90,
  openalex: 0.88,
  crossref: 0.85,
  yandex: 0.86,
  yandex_images: 0.85,
  products: 0.84,
  youtube: 0.85,
  hn: 0.84,
  commons: 0.82,
  rss: 0.82,
  gdelt: 0.80,
  openverse: 0.78,
  ccindex: 0.72,
  archive: 0.72,
};
