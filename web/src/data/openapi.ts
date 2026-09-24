/**
 * OpenAPI 3.1 description of the Antirot public API (formerly Capybara / RatSearch).
 * Served at /api/openapi.json and rendered by /api-docs.
 */

export const openapiSpec = {
  openapi: '3.1.0',
  info: {
    title: 'Antirot Search API',
    version: '1.1.0',
    description:
      'Human-knowledge search API. No AI-generated content: every item passes a ' +
      'defence-in-depth slop gate (pipeline filter, index-time hard fail, purge, ' +
      'query-time rescreen). Local results are BM25-ranked (Tantivy when configured, ' +
      'SQLite FTS5 otherwise, FTS5 for operator queries); verticals federate vetted ' +
      'external providers. SERP features: knowledge panel, featured answer, ' +
      'people-also-ask, sitelinks, did-you-mean, query operators, recency filter.',
  },
  servers: [{ url: '/', description: 'Same origin' }],
  paths: {
    '/api/search': {
      get: {
        summary: 'Federated search',
        description:
          'Searches the local human-knowledge index and federates providers for the ' +
          'selected vertical. Social platforms and AI content farms are blocked at the ' +
          'gateway; news is restricted to a reputable-outlet allowlist. ' +
          'Query operators are supported in `q`: "exact phrase", -exclusion, ' +
          'site:example.com, filetype:pdf, after:YYYY-MM-DD, before:YYYY-MM-DD. ' +
          'Single-word lookups return a `dictionary` instant answer (Wiktionary ' +
          'definitions + IPA + pronunciation audio served by /api/dict/audio). ' +
          'Rate limit: 30 requests/minute per IP.',
        parameters: [
          { name: 'q', in: 'query', required: true, description: 'Search query (max 200 chars), may include operators.', schema: { type: 'string', maxLength: 200 } },
          { name: 'vertical', in: 'query', required: false, description: 'Result vertical.', schema: { type: 'string', enum: ['all', 'images', 'videos', 'news', 'code'], default: 'all' } },
          { name: 'category', in: 'query', required: false, description: 'Local-index category filter (e.g. science_research, programming_engineering).', schema: { type: 'string' } },
          { name: 'when', in: 'query', required: false, description: 'Recency filter on published_date.', schema: { type: 'string', enum: ['any', 'day', 'week', 'month', 'year'], default: 'any' } },
          { name: 'page', in: 'query', required: false, description: 'Page number (1-based). Deep paging is capped.', schema: { type: 'integer', minimum: 1, default: 1 } },
          { name: 'limit', in: 'query', required: false, description: 'Results per page.', schema: { type: 'integer', minimum: 1, maximum: 50, default: 20 } },
        ],
        responses: {
          '200': {
            description: 'Ranked results',
            content: {
              'application/json': {
                example: {
                  query: 'machine learning',
                  vertical: 'all',
                  total_results: 5,
                  page: 1,
                  pages_available: 5,
                  duration_ms: 42,
                  pagination: true,
                  knowledge_card: {
                    title: 'Machine learning', url: 'https://en.wikipedia.org/wiki/Machine learning',
                    provider: 'wikidata', entity_id: 'Q2539',
                    image_url: 'https://commons.wikimedia.org/wiki/Special:FilePath/ML_example.png?width=420',
                    attributes: [{ label: 'Subclass of', value: 'Artificial intelligence' }],
                    sitelinks: [{ title: 'Wikipedia', url: 'https://en.wikipedia.org/wiki/Machine_learning' }],
                  },
                  answer: { text: 'Machine learning is a field of study...', url: 'https://en.wikipedia.org/wiki/Machine_learning', title: 'Machine learning', source_name: 'Wikipedia' },
                  dictionary: {
                    word: 'human', phonetic: '/ˈhjuː.mən/',
                    audio_url: 'https://commons.wikimedia.org/wiki/Special:Redirect/file/En-us-human.ogg',
                    definitions: [{ pos: 'adjective', text: 'Of or belonging to the species Homo sapiens...' }],
                    source: 'wiktionary', found: true,
                  },
                  people_also_ask: [{ question: 'What is machine learning?', answer: '...', url: 'https://en.wikipedia.org/wiki/Machine_learning', source_name: 'Wikipedia' }],
                  operators: [{ label: 'site:arxiv.org', kind: 'site', value: 'arxiv.org' }],
                  when: 'any',
                  did_you_mean: null,
                  related: ['neural network', 'artificial intelligence'],
                  providers: [{ name: 'local', count: 1, ms: 3, engine: 'tantivy' }],
                  results: [{
                    id: 'local-12', title: 'Machine learning', url: 'https://en.wikipedia.org/wiki/Machine_learning',
                    snippet: 'Machine learning is a field of study...', provider: 'local', result_type: 'web',
                    source_name: 'Wikipedia', similarity_score: 0.947, engine: 'tantivy',
                    sitelinks: [{ title: 'Deep learning', url: 'https://en.wikipedia.org/wiki/Deep_learning' }],
                  }],
                  interpretation: { tokens: ['machine', 'learning'], expanded: ['neural'], category: 'programming_engineering' },
                },
              },
            },
          },
          '400': { description: 'Invalid query' },
          '429': { description: 'Rate limited (Retry-After: 60)' },
          '504': { description: 'Search timed out' },
        },
      },
    },
    '/api/suggest': {
      get: {
        summary: 'Autocomplete suggestions',
        description: 'Prefix suggestions from indexed titles blended with aggregated popular queries. No personal data is logged.',
        parameters: [
          { name: 'q', in: 'query', required: true, description: 'Prefix (2-100 chars).', schema: { type: 'string', minLength: 2, maxLength: 100 } },
          { name: 'limit', in: 'query', required: false, description: 'Maximum suggestions.', schema: { type: 'integer', minimum: 1, maximum: 20, default: 8 } },
        ],
        responses: {
          '200': {
            description: 'Suggestions',
            content: {
              'application/json': {
                example: { suggestions: [{ title: 'machine learning', url: '' }, { title: 'Machine learning', url: 'https://en.wikipedia.org/wiki/Machine_learning' }] },
              },
            },
          },
        },
      },
    },
    '/api/stats': {
      get: {
        summary: 'Index statistics',
        description: 'Item counts by category and top sources.',
        responses: {
          '200': {
            description: 'Stats',
            content: {
              'application/json': {
                example: { total_items: 193, categories: { science_research: 55 }, top_sources: [{ name: 'Wikipedia', count: 142, avg_quality: 0.95 }] },
              },
            },
          },
        },
      },
    },
    '/api/categories': {
      get: {
        summary: 'Local index categories',
        responses: {
          '200': {
            description: 'Categories',
            content: {
              'application/json': {
                example: { categories: [{ id: 'science_research', name: 'Science & Research' }] },
              },
            },
          },
        },
      },
    },
    '/api/dict/audio': {
      get: {
        summary: 'Pronunciation audio proxy',
        description:
          'Streams a pronunciation file from an allowlisted host ' +
          '(Wikimedia Commons/Wikimedia uploads, dictionaryapi.com, merriam-webster.com) ' +
          'with a one-year immutable cache. SSRF-safe: https only, host allowlist,2 MB cap.',
        parameters: [
          { name: 'u', in: 'query', required: true, description: 'Absolute https URL on an allowlisted host.', schema: { type: 'string', format: 'uri' } },
        ],
        responses: {
          '200': { description: 'Audio bytes (Content-Type from upstream, max-age=31536000)' },
          '400': { description: 'Host not allowed' },
          '404': { description: 'Missing URL' },
          '413': { description: 'Audio too large' },
          '502': { description: 'Upstream failure' },
          '504': { description: 'Upstream timeout' },
        },
      },
    },
    '/api/openapi.json': {
      get: {
        summary: 'This specification',
        responses: { '200': { description: 'OpenAPI 3.1 document' } },
      },
    },
  },
} as const;

export type OpenApiSpec = typeof openapiSpec;
