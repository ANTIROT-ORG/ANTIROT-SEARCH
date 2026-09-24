import { fetchJson, stripHtml, truncate } from './http';
import { getCachedEntity, putCachedEntity } from '../turso';
import type { ProviderResult } from './types';

/**
 * Knowledge panel — Wikidata entity with claims, image and sitelinks.
 * Results are cached in the local knowledge_entities table (7-day TTL),
 * so the panel stays fast and survives Wikidata outages.
 */

const WD_API = 'https://www.wikidata.org/w/api.php';
const CACHE_TTL_DAYS = 7;

interface WdSearchResponse {
  search?: { id: string; label?: string; description?: string; concepturi?: string }[];
}

interface WdSnak {
  snaktype?: string;
  datavalue?: {
    type?: string;
    value?: any;
  };
}

interface WdClaim {
  mainsnak?: WdSnak;
  rank?: string;
}

interface WdEntity {
  id?: string;
  type?: string;
  labels?: Record<string, { value: string }>;
  descriptions?: Record<string, { value: string }>;
  claims?: Record<string, WdClaim[]>;
  sitelinks?: Record<string, { title?: string; site?: string }>;
}

interface WdEntitiesResponse {
  entities?: Record<string, WdEntity>;
}

/** Curated claim whitelist, in display order. */
const PROPERTIES: { id: string; label: string }[] = [
  { id: 'P31', label: 'Instance of' },
  { id: 'P279', label: 'Subclass of' },
  { id: 'P569', label: 'Born' },
  { id: 'P570', label: 'Died' },
  { id: 'P27', label: 'Nationality' },
  { id: 'P106', label: 'Occupation' },
  { id: 'P39', label: 'Position held' },
  { id: 'P103', label: 'Native language' },
  { id: 'P17', label: 'Country' },
  { id: 'P36', label: 'Capital' },
  { id: 'P30', label: 'Continent' },
  { id: 'P571', label: 'Inception' },
  { id: 'P112', label: 'Founder' },
  { id: 'P117', label: 'Made of' },
  { id: 'P157', label: 'CEO' },
  { id: 'P108', label: 'Employer' },
  { id: 'P159', label: 'Headquarters' },
  { id: 'P276', label: 'Location' },
  { id: 'P407', label: 'Language of work' },
  { id: 'P364', label: 'Original language' },
  { id: 'P921', label: 'Main subject' },
  { id: 'P912', label: 'Has asset' },
  { id: 'P2048', label: 'Height' },
  { id: 'P2046', label: 'Population' },
  { id: 'P1590', label: 'Number of' },
  { id: 'P856', label: 'Official website' },
  { id: 'P496', label: 'ORCID' },
  { id: 'P214', label: 'VIAF' },
  { id: 'P244', label: 'Library of Congress' },
  { id: 'P2002', label: 'X username' },
  { id: 'P2397', label: 'Bluesky username' },
  { id: 'P2013', label: 'Instagram username' },
];

const IMAGE_PROP = 'P18';

const UNIT_LABELS: Record<string, string> = {
  Q11573: 'm', Q98848856: 'kg', Q418: 'rad', Q25342: '°C',
  Q99498512: 'km', Q1007507: 'ha', Q8146: 'USD', Q4917: 'USD',
};

const TIME_MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function formatTime(raw: string): string {
  const m = String(raw).replace(/^[+-]/, '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return String(raw);
  const [, y, mo, d] = m;
  if (mo === '00' || d === '00') return y;
  return `${parseInt(d, 10)} ${TIME_MONTHS[parseInt(mo, 10) - 1]} ${y}`;
}

function formatQuantity(value: any): string {
  const amount = String(value?.amount ?? '').replace(/^\+/, '');
  if (!amount) return '';
  const unitId = String(value?.unit || '').split('/').pop() || '';
  const unit = UNIT_LABELS[unitId];
  return unit ? `${amount} ${unit}` : amount;
}

function commonsThumb(filename: string, width = 420): string {
  return `https://commons.wikimedia.org/wiki/Special:FilePath/${encodeURIComponent(filename)}?width=${width}`;
}

function wikiUrlFor(site: string, title: string): string | null {
  const encoded = encodeURIComponent(title.replace(/ /g, '_'));
  if (site === 'enwiki') return `https://en.wikipedia.org/wiki/${encoded}`;
  if (site === 'wikidatawiki') return `https://www.wikidata.org/wiki/${encoded}`;
  if (site === 'commonswiki') return `https://commons.wikimedia.org/wiki/${encoded}`;
  const m = site.match(/^([a-z-]{2,12})wiki$/);
  if (m) return `https://${m[1]}.wikipedia.org/wiki/${encoded}`;
  return null;
}

/** Human labels for Wikipedia language subdomains (panel sitelinks). */
const WIKI_LANG: Record<string, string> = {
  en: 'English', de: 'Deutsch', fr: 'Français', es: 'Español', it: 'Italiano',
  pt: 'Português', ru: 'Русский', ja: '日本語', zh: '中文', nl: 'Nederlands',
  sv: 'Svenska', pl: 'Polski', tr: 'Türkçe', ar: 'العربية', fa: 'فارسی',
  hi: 'हिन्दी', bn: 'বাংলা', ko: '한국어', id: 'Indonesia', uk: 'Українська',
  cs: 'Čeština', fi: 'Suomi', da: 'Dansk', no: 'Norsk', hu: 'Magyar',
  ro: 'Română', el: 'Ελληνικά', he: 'עברית', vi: 'Tiếng Việt', th: 'ไทย',
  ca: 'Català', eo: 'Esperanto', sa: 'संस्कृतम्', ta: 'தமிழ்', hy: 'Հայերեն',
  be: 'Беларуская', bg: 'Български', hr: 'Hrvatski', sk: 'Slovenčina',
  sl: 'Slovenščina', lt: 'Lietuvių', lv: 'Latviešu', et: 'Eesti',
  is: 'Íslenska', ga: 'Gaeilge', cy: 'Cymraeg', mt: 'Malti', sq: 'Shqip',
  mk: 'Македонски', sr: 'Српски', eu: 'Euskara', gl: 'Galego',
  uz: 'Oʻzbekcha', kk: 'Қазақша', hye: 'Հայերեն', simple: 'Simple English',
};

/** Preferred order for non-English sitelinks on the panel. */
const WIKI_PRIORITY = [
  'dewiki', 'jawiki', 'frwiki', 'eswiki', 'ruwiki', 'zhwiki', 'itwiki',
  'ptwiki', 'nlwiki', 'plwiki', 'commonswiki', 'wikidatawiki', 'simplewiki',
];

function sitelinkLabel(site: string): string {
  if (site === 'wikidatawiki') return 'Wikidata';
  if (site === 'commonswiki') return 'Commons';
  if (site === 'abstractwiki') return 'Abstract Wikipedia';
  const m = site.match(/^([a-z-]{2,12})wiki$/);
  if (!m) return site;
  return WIKI_LANG[m[1]] || `${m[1].toUpperCase()} Wikipedia`;
}

function pickBestHit(hits: WdSearchResponse['search'], query: string): NonNullable<WdSearchResponse['search']>[number] | null {
  const items = (hits || []).filter(h => /^Q\d+$/.test(h.id || ''));
  if (!items.length) return null;
  const q = query.trim().toLowerCase();
  return (
    items.find(h => (h.label || '').toLowerCase() === q) ||
    items.find(h => (h.label || '').toLowerCase().startsWith(q)) ||
    items.find(h => (h.label || '').toLowerCase().includes(q)) ||
    items[0]
  );
}

/** Resolve claim values, batching entity-label lookups in a second request. */
async function buildAttributes(entity: WdEntity): Promise<{ label: string; value: string; url?: string }[]> {
  const claims = entity.claims || {};
  const pendingEntityIds = new Set<string>();
  type Staged = { label: string; value?: string; url?: string; entityId?: string; time?: string; quantity?: string };
  const staged: Staged[] = [];

  const push = (label: string, entry: Omit<Staged, 'label'>) => staged.push({ label, ...entry });

  for (const prop of PROPERTIES) {
    const list = claims[prop.id];
    if (!list?.length) continue;
    // Prefer preferred-rank claims, then normal
    const preferred = list.filter(c => c.rank === 'preferred');
    const chosen = (preferred.length ? preferred : list).slice(0, prop.id === 'P31' ? 3 : 2);

    for (const claim of chosen) {
      const snak = claim.mainsnak;
      if (!snak || snak.snaktype === 'novalue') continue;
      const dv = snak.datavalue;
      if (!dv?.value && dv?.value !== 0) continue;
      const value = dv.value;

      if (dv.type === 'wikibase-entityid' || value?.['entity-type']) {
        const id = String(value.id ?? '');
        if (!/^Q\d+$/.test(id)) continue;
        pendingEntityIds.add(id);
        push(prop.label, { value: '', entityId: id });
      } else if (dv.type === 'time' && typeof value?.time === 'string') {
        push(prop.label, { time: formatTime(value.time) });
      } else if (dv.type === 'quantity') {
        push(prop.label, { quantity: formatQuantity(value) });
      } else if (typeof value === 'string') {
        const url = prop.id === 'P856' ? value.replace(/^http:/, 'https:') : undefined;
        push(prop.label, { value: stripHtml(value).trim(), url });
      } else if (value?.text) {
        push(prop.label, { value: stripHtml(String(value.text)).trim() });
      }
    }
  }

  // Batch-resolve entity labels
  let labels: Record<string, { value: string }> = {};
  const ids = [...pendingEntityIds];
  for (let i = 0; i < ids.length; i += 40) {
    const chunk = ids.slice(i, i + 40);
    const res = await fetchJson<WdEntitiesResponse>(
      `${WD_API}?action=wbgetentities&ids=${encodeURIComponent(chunk.join('|'))}` +
      `&props=labels&languages=en&format=json&origin=*`,
      { timeoutMs: 4000 }
    );
    for (const [id, ent] of Object.entries(res?.entities || {})) {
      if (ent?.labels?.en) labels[id] = ent.labels.en;
    }
  }

  const attributes: { label: string; value: string; url?: string }[] = [];
  const seenKeys = new Set<string>();
  for (const entry of staged) {
    let value = entry.value;
    if (entry.entityId) value = labels[entry.entityId]?.value || '';
    if (entry.time) value = entry.time;
    if (entry.quantity) value = entry.quantity;
    if (!value) continue;
    const key = `${entry.label}:${value}`;
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    attributes.push({ label: entry.label, value, url: entry.url });
  }
  return attributes.slice(0, 14);
}

function buildSitelsinks(entity: WdEntity): { title: string; url: string }[] {
  const sitelinks = entity.sitelinks || {};
  const out: { title: string; url: string }[] = [];
  const enwiki = sitelinks.enwiki?.title;
  if (enwiki) out.push({ title: 'Wikipedia', url: wikiUrlFor('enwiki', enwiki) || '' });

  const candidates = Object.entries(sitelinks).filter(
    ([site, entry]) => site !== 'enwiki' && !!entry?.title
  );
  candidates.sort(([a], [b]) => {
    const pa = WIKI_PRIORITY.indexOf(a);
    const pb = WIKI_PRIORITY.indexOf(b);
    return (pa === -1 ? 99 : pa) - (pb === -1 ? 99 : pb);
  });

  for (const [site, entry] of candidates) {
    const url = wikiUrlFor(site, entry!.title!);
    if (!url) continue;
    const label = sitelinkLabel(site);
    if (out.some(l => l.title === label)) continue;
    out.push({ title: label, url });
    if (out.length >= 5) break;
  }
  return out.filter(l => l.url);
}

export async function knowledgeCard(q: string): Promise<ProviderResult | null> {
  const query = q.trim();
  if (!query) return null;
  const queryKey = query.toLowerCase().replace(/\s+/g, ' ').slice(0, 120);

  // 1. Local entity cache (7-day TTL)
  try {
    const cached = await getCachedEntity(queryKey);
    if (cached) {
      return JSON.parse(cached.props_json) as ProviderResult;
    }
  } catch { /* cache miss / table missing */ }

  // 2. Live Wikidata lookup
  const found = await fetchJson<WdSearchResponse>(
    `${WD_API}?action=wbsearchentities&search=${encodeURIComponent(query)}` +
    `&language=en&uselang=en&format=json&limit=5&origin=*`,
    { timeoutMs: 4000 }
  );
  const hit = pickBestHit(found?.search, query);
  if (!hit) return null;

  const detail = await fetchJson<WdEntitiesResponse>(
    `${WD_API}?action=wbgetentities&ids=${encodeURIComponent(hit.id)}` +
    `&props=type|labels|descriptions|claims|sitelinks&languages=en&uselang=en&format=json&origin=*`,
    { timeoutMs: 5000 }
  );
  const entity = detail?.entities?.[hit.id];
  if (!entity || (entity.type && entity.type !== 'item')) return null;

  const label = entity.labels?.en?.value || hit.label || hit.id;
  const description = entity.descriptions?.en?.value || hit.description || '';

  const imageClaim = entity.claims?.[IMAGE_PROP]?.[0]?.mainsnak?.datavalue?.value;
  const imageFile = typeof imageClaim === 'string' ? imageClaim : '';

  const [attributes, sitelinks] = await Promise.all([
    buildAttributes(entity),
    Promise.resolve(buildSitelsinks(entity)),
  ]);

  const wikiLink = sitelinks.find(l => l.title === 'Wikipedia');
  const url = wikiLink?.url || (hit.concepturi || `https://www.wikidata.org/wiki/${hit.id}`).replace(/^http:/, 'https:');

  const card: ProviderResult & {
    entity_id: string;
    image_url?: string;
    attributes: { label: string; value: string; url?: string }[];
  } = {
    id: `wikidata-${hit.id}`,
    entity_id: hit.id,
    title: label,
    url,
    snippet: truncate(stripHtml(description), 240),
    provider: 'wikidata',
    result_type: 'knowledge',
    source_name: 'Wikidata',
    image_url: imageFile ? commonsThumb(imageFile) : undefined,
    thumbnail: imageFile ? commonsThumb(imageFile, 120) : undefined,
    attributes,
    sitelinks: sitelinks.filter(l => l.url),
  };

  // 3. Persist to the local entity cache (best-effort)
  try {
    await putCachedEntity({
      qid: hit.id,
      label,
      description: stripHtml(description),
      entity_type: 'item',
      props_json: JSON.stringify(card),
      image_url: card.image_url || '',
      wiki_url: url,
      query_key: queryKey,
    });
  } catch { /* cache write is best-effort */ }

  return card;
}

export { CACHE_TTL_DAYS };
