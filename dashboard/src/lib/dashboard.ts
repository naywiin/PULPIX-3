import type { RawBlock, RawDashboard } from '../api/backend';
import type { ClientConfig } from '../config/clients';
import type {
  AdsPeriod,
  ApiClient,
  DailyAdsPoint,
  DashboardData,
  Ga4Metrics,
  Ga4Period,
  AdSourceResult,
  BlockWarning,
  Ga4Property,
  Ga4Row,
  PlatformBlock,
} from '../types/dashboard';
import type { CampaignObjective, MetricRecord, Platform } from '../types/metrics';
import { deriveMetrics, round2, safeRatio, type Totals } from './metrics';
import { metaResultMode } from '../config/valueMetrics';

/* ------------------------------------------------------------------ clientes */

const FALLBACK_COLORS = ['#0f766e', '#1d4ed8', '#7c3aed', '#b45309', '#be185d', '#15803d', '#0369a1', '#9333ea'];

function colorFor(id: string): string {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return FALLBACK_COLORS[h % FALLBACK_COLORS.length];
}

/** Adapta un cliente del registro central al modelo que usan los componentes. */
export function clientConfigFromApi(c: ApiClient): ClientConfig {
  const src = (accountId: string | null) => (accountId ? { accountId, mode: 'api' as const } : undefined);
  return {
    id: c.id,
    name: c.name,
    reportName: 'Reporte de Performance · Paid Media',
    brandColor: c.brand_color || colorFor(c.id),
    logoUrl: c.logo ?? undefined,
    currency: c.currency || 'MXN',
    locale: 'es-MX',
    timezone: c.timezone || 'America/Mexico_City',
    sources: {
      google_ads: src(c.google_ads_customer_id),
      meta_ads: src(c.meta_ad_account_id),
      spotify_ads: src(c.spotify_ads_sources?.[0]?.ad_account_id ?? null),
      pinterest_ads: src(c.pinterest_ads_sources?.[0]?.ad_account_id ?? null),
      // Multi-propiedad: el bloque real lleva todas en propertyIds; aquí
      // basta la primera para saber que GA4 está conectado.
      google_analytics: src(c.ga4_property_ids?.[0] ?? c.ga4_property_id),
    },
    hiddenMetrics: c.hidden_metrics ?? [],
  };
}

/* ------------------------------------------------------- normalización API */

const OBJECTIVES: CampaignObjective[] = ['SALES', 'LEADS', 'TRAFFIC', 'AWARENESS', 'ENGAGEMENT', 'OTHER'];
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : Number(v) || 0);

type Row = Record<string, unknown>;

function toRecord(row: Row, ctx: { clientId: string; platform: Platform; accountId: string; start: string; end: string }): MetricRecord {
  const base = {
    impressions: num(row.impressions),
    reach: num(row.reach),
    clicks: num(row.clicks),
    spend: num(row.spend),
    conversions: num(row.conversions),
    conversion_value: num(row.conversion_value),
    inline_link_clicks: row.inline_link_clicks == null ? undefined : num(row.inline_link_clicks),
  };
  const derived = deriveMetrics(base);
  const objective = OBJECTIVES.includes(row.objective as CampaignObjective) ? (row.objective as CampaignObjective) : undefined;
  return {
    ...derived,
    leads: row.leads == null ? null : num(row.leads),
    purchases: row.purchases == null ? null : num(row.purchases),
    cost_per_result: safeRatio(base.spend, row.leads == null ? null : num(row.leads)),
    cost_per_purchase: safeRatio(base.spend, row.purchases == null ? null : num(row.purchases)),
    checkouts: row.checkouts == null ? null : num(row.checkouts),
    ...(ctx.platform === 'pinterest_ads' ? {
      pinterestSpend: row.spend == null ? null : num(row.spend),
      pinterestImpressions: row.impressions == null ? null : num(row.impressions),
      pinterestClicks: row.clicks == null ? null : num(row.clicks),
      pinterestCtr: row.ctr == null ? null : num(row.ctr),
      pinterestCpc: row.cpc == null ? null : num(row.cpc),
      pinterestConversionValue: row.conversion_value == null ? null : num(row.conversion_value),
      pinterestRoas: row.roas == null ? null : num(row.roas),
    } : {}),
    value_available: row.value_available === false ? false : undefined,
    // Meta entrega la frecuencia nativa del periodo; se respeta si viene.
    frequency: row.frequency != null && num(row.frequency) > 0 ? num(row.frequency) : derived.frequency,
    client_id: ctx.clientId,
    platform: ctx.platform,
    account_id: ctx.accountId,
    campaign_id: String(row.campaign_id ?? ''),
    campaign_name: String(row.campaign_name ?? '(sin nombre)'),
    status: row.status == null ? null : String(row.status),
    effective_status: row.effective_status == null ? null : String(row.effective_status),
    ad_set_id: row.ad_set_id != null ? String(row.ad_set_id) : undefined,
    ad_set_name: row.ad_set_name != null ? String(row.ad_set_name) : undefined,
    ad_id: row.ad_id != null ? String(row.ad_id) : undefined,
    ad_name: row.ad_name != null ? String(row.ad_name) : undefined,
    business_id: row.business_id != null ? String(row.business_id) : undefined,
    completes: row.completes == null ? undefined : num(row.completes),
    starts: row.starts == null ? undefined : num(row.starts),
    first_quartiles: row.first_quartiles == null ? undefined : num(row.first_quartiles),
    midpoints: row.midpoints == null ? undefined : num(row.midpoints),
    third_quartiles: row.third_quartiles == null ? undefined : num(row.third_quartiles),
    video_views: row.video_views == null ? undefined : num(row.video_views),
    video_expands: row.video_expands == null ? undefined : num(row.video_expands),
    video_expand_rate: row.video_expand_rate == null ? undefined : num(row.video_expand_rate),
    objective,
    // Campaña consumida solo por algunos de sus conjuntos de anuncios.
    partial: row.partial === true ? true : undefined,
    selected_adsets: row.selected_adsets == null ? undefined : num(row.selected_adsets),
    // De qué cuenta viene la campaña: se conserva siempre, aunque la tabla no
    // lo muestre, para poder rastrear cada fila hasta su cuenta de origen.
    source_account_id: row.source_account_id != null ? String(row.source_account_id) : ctx.accountId,
    source_account_name: row.source_account_name != null ? String(row.source_account_name) : undefined,
    date: row.date != null ? String(row.date) : ctx.start,
    date_end: row.date_end != null ? String(row.date_end) : ctx.end,
  };
}

function toTotals(row: Row | undefined): Totals {
  const t: Totals = deriveMetrics({
    impressions: num(row?.impressions),
    reach: num(row?.reach),
    clicks: num(row?.clicks),
    spend: round2(num(row?.spend)),
    conversions: num(row?.conversions),
    conversion_value: round2(num(row?.conversion_value)),
    inline_link_clicks: num(row?.inline_link_clicks),
  });
  if (row?.frequency != null && num(row.frequency) > 0) t.frequency = num(row.frequency);
  t.leads = row?.leads == null ? null : num(row.leads);
  t.purchases = row?.purchases == null ? null : num(row.purchases);
  t.cost_per_result = safeRatio(t.spend, t.leads);
  t.cost_per_purchase = safeRatio(t.spend, t.purchases);
  t.spend_available = row?.spend != null;
  t.clicks_available = row?.clicks != null;
  t.conversions_available = row?.conversions != null;
  t.conversion_value_available = row?.conversion_value != null && row?.value_available !== false;
  t.value_available = t.conversion_value_available;
  return t;
}

function toDaily(rows: unknown): DailyAdsPoint[] {
  if (!Array.isArray(rows)) return [];
  return (rows as Row[]).map((r) => ({
    date: String(r.date),
    spend: num(r.spend),
    conversions: r.conversions == null ? null : num(r.conversions),
    leads: r.leads == null ? null : num(r.leads),
    purchases: r.purchases == null ? null : num(r.purchases),
    conversion_value: r.conversion_value == null ? null : num(r.conversion_value),
    clicks: num(r.clicks),
    impressions: num(r.impressions),
  }));
}

function pinterestMetrics(raw: unknown): AdsPeriod['pinterestTotals'] {
  if (!raw || typeof raw !== 'object') return undefined;
  const row = raw as Row;
  const nullable = (key: string) => row[key] == null ? null : num(row[key]);
  return {
    spend: nullable('spend'), impressions: nullable('impressions'), clicks: nullable('clicks'),
    ctr: nullable('ctr'), cpc: nullable('cpc'), checkouts: nullable('checkouts'),
    cost_per_checkout: nullable('cost_per_checkout'), conversion_value: nullable('conversion_value'),
    roas: nullable('roas'), currency: row.currency == null ? null : String(row.currency),
  };
}

function adsPeriod(raw: Row | null | undefined, ctx: Parameters<typeof toRecord>[1]): AdsPeriod | null {
  if (!raw) return null;
  const rows = (k: string) => (Array.isArray(raw[k]) ? (raw[k] as Row[]) : []);
  return {
    totals: toTotals(raw.totals as Row | undefined),
    campaigns: rows('campaigns').map((r) => toRecord(r, ctx)),
    adSets: rows('adsets').map((r) => toRecord(r, ctx)),
    ads: rows('ads').map((r) => toRecord(r, ctx)),
    daily: toDaily(raw.daily),
    sourcesCount: raw.sources_count == null ? undefined : num(raw.sources_count),
    reachIsSum: Boolean(raw.reach_is_sum),
    checkouts: raw.totals && typeof raw.totals === 'object' && (raw.totals as Row).checkouts != null
      ? num((raw.totals as Row).checkouts) : null,
    pinterestTotals: ctx.platform === 'pinterest_ads' ? pinterestMetrics(raw.totals) : undefined,
    pinterestDaily: ctx.platform === 'pinterest_ads'
      ? rows('daily').map((r) => ({ date: String(r.date), ...pinterestMetrics(r)! })) : undefined,
  };
}

/** Resultado por cuenta de origen (vista "de dónde salen los datos"). */
function adSources(raw: unknown): AdSourceResult[] {
  if (!Array.isArray(raw)) return [];
  return (raw as Row[]).map((r) => ({
    accountId: String(r.account_id ?? ''),
    accountName: r.account_name == null ? null : String(r.account_name),
    campaignIds: Array.isArray(r.campaign_ids) ? (r.campaign_ids as unknown[]).map(String) : [],
    allocation: (r.allocation as AdSourceResult['allocation']) ?? 'exclusive',
    level: r.level as AdSourceResult['level'],
    adsetSelections: Array.isArray(r.adset_selections)
      ? (r.adset_selections as Row[]).map((sel) => ({
          campaign_id: String(sel.campaign_id ?? ''),
          adset_ids: Array.isArray(sel.adset_ids) ? (sel.adset_ids as unknown[]).map(String) : [],
          allocation: (sel.allocation as AdSourceResult['allocation']) ?? 'exclusive',
        }))
      : undefined,
    effective: r.effective as AdSourceResult['effective'],
    status: (r.status as AdSourceResult['status']) ?? 'error',
    message: r.message == null ? undefined : String(r.message),
    detail: r.detail == null ? undefined : String(r.detail),
    currency: r.currency == null ? null : String(r.currency),
  }));
}

function ga4Metrics(r: Row | undefined): Ga4Metrics {
  return {
    users: num(r?.users),
    sessions: num(r?.sessions),
    engaged_sessions: r?.engaged_sessions == null ? null : num(r.engaged_sessions),
    engagement_rate: r?.engagement_rate == null ? null : num(r.engagement_rate),
    key_events: num(r?.key_events),
    revenue: num(r?.revenue),
  };
}

function ga4Period(raw: Row | null | undefined): Ga4Period | null {
  if (!raw) return null;
  const list = (k: string): Ga4Row[] =>
    Array.isArray(raw[k]) ? (raw[k] as Row[]).map((r) => ({ name: String(r.name ?? ''), ...ga4Metrics(r) })) : [];
  return {
    totals: ga4Metrics(raw.totals as Row | undefined),
    daily: Array.isArray(raw.daily) ? (raw.daily as Row[]).map((r) => ({ date: String(r.date), ...ga4Metrics(r) })) : [],
    by_source_medium: list('by_source_medium'),
    by_campaign: list('by_campaign'),
    by_landing_page: list('by_landing_page'),
    propertiesCount: raw.properties_count == null ? undefined : num(raw.properties_count),
  };
}

/** Datos por propiedad: cada una conserva su estado, su nombre y su moneda. */
function ga4Properties(raw: unknown): Ga4Property[] {
  if (!Array.isArray(raw)) return [];
  return (raw as Row[]).map((p) => ({
    propertyId: String(p.property_id ?? ''),
    propertyName: p.property_name == null ? null : String(p.property_name),
    currency: p.currency == null ? null : String(p.currency),
    status: (p.status as Ga4Property['status']) ?? 'error',
    message: p.message == null ? undefined : String(p.message),
    detail: p.detail == null ? undefined : String(p.detail),
    current: ga4Period(p.current as Row | null | undefined),
    previous: ga4Period(p.previous as Row | null | undefined),
  }));
}

function blockWarnings(raw: unknown, fallback: string): BlockWarning[] {
  if (!Array.isArray(raw)) return [];
  return (raw as Row[]).map((w) => ({
    propertyId: w.property_id == null ? null : String(w.property_id),
    accountId: w.account_id == null ? null : String(w.account_id),
    status: String(w.status ?? 'error'),
    message: String(w.message ?? fallback),
    detail: w.detail == null ? undefined : String(w.detail),
  }));
}

function blockBase<P>(b: RawBlock, fallbackCurrency: string): Omit<PlatformBlock<P>, 'current' | 'previous'> {
  return {
    platform: b.platform as PlatformBlock<P>['platform'],
    label: b.label,
    status: b.status,
    source: 'real',
    accountId: b.account_id,
    currency: b.currency || fallbackCurrency,
    message: b.message,
    detail: b.detail,
  };
}

/**
 * Bloque GA4: consolidado (current/previous) + cada propiedad por separado.
 * Una propiedad caída llega como aviso y el bloque sigue en estado "ok".
 */
function ga4Block(b: RawBlock, client: ApiClient, hasCompare: boolean): PlatformBlock<Ga4Period> {
  // property_ids es lo que manda la versión nueva; si no viene (backend previo)
  // se usa el account_id suelto para no perder la propiedad única.
  const propertyIds = b.property_ids ?? (b.account_id ? [b.account_id] : []);
  return {
    ...blockBase<Ga4Period>(b, client.currency),
    propertyIds,
    properties: ga4Properties(b.properties),
    warnings: blockWarnings(b.warnings, 'Propiedad no disponible'),
    mixedCurrency: Boolean(b.mixed_currency),
    current: b.status === 'ok' ? ga4Period(b.current) : null,
    previous: b.status === 'ok' && hasCompare ? ga4Period(b.previous) : null,
  };
}

export function normalizeDashboard(raw: RawDashboard): DashboardData {
  const clientId = raw.client.id;
  const cur = raw.range;
  const prev = raw.compare_range;
  const ads = (b: RawBlock, platform: Platform): PlatformBlock<AdsPeriod> => {
    const ctx = (r: { start: string; end: string }) => ({ clientId, platform, accountId: b.account_id ?? '', start: r.start, end: r.end });
    return {
      ...blockBase<AdsPeriod>(b, raw.client.currency),
      sources: adSources(b.sources),
      warnings: blockWarnings(b.warnings, 'Cuenta no disponible'),
      mixedCurrency: Boolean(b.mixed_currency),
      current: (b.status === 'ok' || (platform === 'pinterest_ads' && b.status === 'partial'))
        ? adsPeriod(b.current, ctx(cur)) : null,
      previous: (b.status === 'ok' || (platform === 'pinterest_ads' && b.status === 'partial')) && prev
        ? adsPeriod(b.previous, ctx(prev)) : null,
    };
  };
  return {
    source: 'real',
    clientId,
    range: cur,
    compareRange: prev,
    generatedAt: raw.generated_at,
    google_ads: ads(raw.platforms.google_ads, 'google_ads'),
    meta_ads: ads(raw.platforms.meta_ads, 'meta_ads'),
    spotify_ads: ads(raw.platforms.spotify_ads ?? {
      platform: 'spotify_ads', label: 'Spotify Ads', status: 'not_linked', source: 'real', account_id: null,
    }, 'spotify_ads'),
    pinterest_ads: ads(raw.platforms.pinterest_ads ?? {
      platform: 'pinterest_ads', label: 'Pinterest Ads', status: 'not_configured', source: 'real', account_id: null,
    }, 'pinterest_ads'),
    ga4: ga4Block(raw.platforms.ga4, raw.client, prev != null),
  };
}

/* ------------------------------------------------------------- combinados */

export interface CombinedTotals {
  spend: number | null;
  conversions: number | null;
  conversion_value: number | null;
  clicks: number | null;
  cpa: number | null;
  cpc: number | null;
  roas: number | null;
  conversion_sources: ('google_ads' | 'meta_ads')[];
  efficiency_sources: ('google_ads' | 'meta_ads' | 'spotify_ads')[];
  conversions_partial: boolean;
  cpa_sources: ('google_ads' | 'meta_ads')[];
  cpa_partial: boolean;
  value_sources: ('google_ads' | 'meta_ads')[];
  value_partial: boolean;
  roas_sources: ('google_ads' | 'meta_ads')[];
  roas_partial: boolean;
}

export interface CombinedResult {
  /** Plataformas que aportaron datos. */
  included: ('google_ads' | 'meta_ads' | 'spotify_ads')[];
  /** Moneda común; null si las plataformas usan monedas distintas (no se suman). */
  currency: string | null;
  current: CombinedTotals | null;
  previous: CombinedTotals | null;
}

function combine(data: DashboardData, previous = false): CombinedTotals {
  const blocks = [data.google_ads, data.meta_ads, data.spotify_ads].filter((b) => b.status === 'ok' && (previous ? b.previous : b.current));
  const metricPeriods = blocks.map((b) => (previous ? b.previous : b.current)!.totals);
  let spend = 0;
  let spendAvailable = metricPeriods.length > 0;
  let clicks = 0;
  let clicksAvailable = metricPeriods.length > 0;
  for (const t of metricPeriods) {
    spend += t.spend;
    clicks += t.clicks;
    if (t.spend_available === false) spendAvailable = false;
    if (t.clicks_available === false) clicksAvailable = false;
  }
  const eventBlocks = blocks.filter((b) => b.platform === 'google_ads' || b.platform === 'meta_ads');
  let conversions = 0;
  const conversionSources: ('google_ads' | 'meta_ads')[] = [];
  const missingEventSources: ('google_ads' | 'meta_ads')[] = [];
  const eventSpends: { platform: 'google_ads' | 'meta_ads'; spend: number; currency: string }[] = [];
  for (const block of eventBlocks) {
    const platform = block.platform as 'google_ads' | 'meta_ads';
    const totals = (previous ? block.previous : block.current)!.totals;
    const eventCount = platform === 'google_ads'
      ? totals.conversions
      : metaResultMode(data.clientId) === 'purchase' ? totals.purchases : totals.leads;
    const available = eventCount != null && Number.isFinite(eventCount)
      && !(platform === 'google_ads' && totals.conversions_available === false);
    if (!available) {
      missingEventSources.push(platform);
      continue;
    }
    conversions += eventCount;
    conversionSources.push(platform);
    if (totals.spend_available !== false && Number.isFinite(totals.spend)) {
      eventSpends.push({ platform, spend: totals.spend, currency: block.currency });
    }
  }
  const currencySet = new Set(blocks.map((b) => b.currency));
  const currencyCompatible = currencySet.size <= 1;
  const cpaCurrencySet = new Set(eventSpends.map((item) => item.currency));
  const cpaCurrencyCompatible = cpaCurrencySet.size <= 1;
  const cpaSources = eventSpends.map((item) => item.platform);
  const cpaSpend = eventSpends.reduce((sum, item) => sum + item.spend, 0);
  const cpaConversions = cpaSources.reduce((sum, platform) => {
    const block = eventBlocks.find((item) => item.platform === platform)!;
    const totals = (previous ? block.previous : block.current)!.totals;
    return sum + (platform === 'google_ads' ? totals.conversions : metaResultMode(data.clientId) === 'purchase' ? totals.purchases : totals.leads)!;
  }, 0);
  const valueBlocks = eventBlocks.filter((block) =>
    block.platform === 'google_ads' || metaResultMode(data.clientId) === 'purchase');
  const valueSources: ('google_ads' | 'meta_ads')[] = [];
  const valueContributors: { platform: 'google_ads' | 'meta_ads'; value: number; spend: number; currency: string }[] = [];
  for (const block of valueBlocks) {
    const platform = block.platform as 'google_ads' | 'meta_ads';
    const totals = (previous ? block.previous : block.current)!.totals;
    if (platform === 'meta_ads' && metaResultMode(data.clientId) === 'purchase' && totals.purchases == null) continue;
    if (totals.conversion_value_available === false || totals.value_available === false
      || !Number.isFinite(totals.conversion_value) || totals.spend_available === false
      || !Number.isFinite(totals.spend)) continue;
    valueSources.push(platform);
    valueContributors.push({ platform, value: totals.conversion_value, spend: totals.spend, currency: block.currency });
  }
  const valueCurrencies = new Set(valueContributors.map((item) => item.currency));
  const valueCurrencyCompatible = valueCurrencies.size <= 1;
  const value = valueContributors.reduce((sum, item) => sum + item.value, 0);
  const roasSources = valueContributors.filter((item) => item.spend !== 0).map((item) => item.platform);
  const roasSpend = valueContributors.filter((item) => item.spend !== 0).reduce((sum, item) => sum + item.spend, 0);
  const roasValue = valueContributors.filter((item) => item.spend !== 0).reduce((sum, item) => sum + item.value, 0);
  const valuePartial = valueSources.length < valueBlocks.length || valueBlocks.length < eventBlocks.length;
  const allClicks = metricPeriods.reduce((sum, t) => sum + t.clicks, 0);
  const allSpendAvailable = spendAvailable && metricPeriods.every((t) => Number.isFinite(t.spend));
  const allClicksAvailable = clicksAvailable && metricPeriods.every((t) => Number.isFinite(t.clicks));
  const cpcSpend = metricPeriods.reduce((sum, t) => sum + t.spend, 0);
  return {
    spend: currencyCompatible && allSpendAvailable ? round2(spend) : null,
    clicks: currencyCompatible && allClicksAvailable ? allClicks : null,
    conversions: conversionSources.length ? conversions : null,
    conversion_value: valueCurrencyCompatible && valueSources.length ? round2(value) : null,
    cpa: cpaCurrencyCompatible && cpaSources.length && cpaConversions !== 0 ? round2(cpaSpend) / cpaConversions : null,
    cpc: currencyCompatible && allSpendAvailable && allClicksAvailable && allClicks !== 0 ? round2(cpcSpend) / allClicks : null,
    roas: valueCurrencyCompatible && roasSpend !== 0 && roasSources.length ? roasValue / roasSpend : null,
    conversion_sources: conversionSources,
    efficiency_sources: blocks.map((b) => b.platform as 'google_ads' | 'meta_ads' | 'spotify_ads'),
    conversions_partial: missingEventSources.length > 0,
    cpa_sources: cpaSources,
    cpa_partial: cpaSources.length < eventBlocks.length || missingEventSources.length > 0,
    value_sources: valueSources,
    value_partial: valuePartial,
    roas_sources: roasSources,
    roas_partial: roasSources.length < eventBlocks.length,
  };
}

/** Totales combinados de las plataformas de paid media con la misma moneda. */
export function combinedTotals(data: DashboardData): CombinedResult {
  const blocks = [data.google_ads, data.meta_ads, data.spotify_ads].filter((b) => b.status === 'ok' && b.current);
  const included = blocks.map((b) => b.platform as 'google_ads' | 'meta_ads' | 'spotify_ads');
  const currencies = new Set(blocks.map((b) => b.currency));
  if (blocks.length === 0) return { included, currency: null, current: null, previous: null };
  const hasPrev = data.compareRange != null && blocks.every((b) => b.previous);
  return {
    included,
    currency: currencies.size <= 1 ? [...currencies][0] : null,
    current: blocks.length ? combine(data) : null,
    previous: hasPrev ? combine(data, true) : null,
  };
}

export type LeadSourceChoice = 'both' | 'google_ads' | 'meta_ads';
export interface LeadPlatformViewItem {
  key: 'google_ads' | 'meta_ads';
  label: string;
  total: number | null;
  daily: (number | null)[];
}

/** El selector de Overview deriva cards y series de la misma selección. */
export function leadPlatformView(data: DashboardData, choice: LeadSourceChoice, dates: string[]): LeadPlatformViewItem[] {
  const blocks = [
    { key: 'google_ads' as const, label: 'Google Ads', block: data.google_ads },
    { key: 'meta_ads' as const, label: 'Meta Ads', block: data.meta_ads },
  ];
  return blocks.filter(({ key, block }) => (choice === 'both' || choice === key) && block.status === 'ok' && block.current)
    .map(({ key, label, block }) => ({
      key, label, total: block.current?.totals.leads ?? null,
      daily: alignDaily(dates, block.current?.daily, 'leads'),
    }));
}

/** Une series diarias de varias plataformas sobre el mismo eje de fechas (días faltantes = 0). */
export function alignDaily(dates: string[], daily: DailyAdsPoint[] | undefined, key: keyof Omit<DailyAdsPoint, 'date'>): (number | null)[] {
  if (!daily) return dates.map(() => null);
  const map = new Map(daily.map((d) => [d.date, d[key]]));
  return dates.map((d) => map.has(d) ? (map.get(d) ?? null) : 0);
}
