import type { ClientConfig } from '../config/clients';
import { eachDay } from '../lib/dates';
import { sumRecords } from '../lib/metrics';
import { queryMock } from '../mock';
import type { AdsPeriod, DailyAdsPoint, DashboardData, Ga4Period, Ga4Row, PlatformBlock } from '../types/dashboard';
import type { MetricRecord, Platform } from '../types/metrics';
import type { DateRange, DateSelection, PeriodData } from '../types/report';
import { fetchPlatformReport } from './reportService';

/**
 * Dashboard construido 100% con los datasets mock locales (fallback de desarrollo).
 * Todo lo que devuelve lleva source: 'mock' y la UI lo marca como MOCK DATA.
 * Nunca se combina con datos reales del backend.
 */

const MAX_DAILY_DAYS = 400;

function dailyMock(clientId: string, platform: Platform, range: DateRange, today: string): DailyAdsPoint[] {
  const days = eachDay(range).filter((d) => d <= today);
  if (days.length > MAX_DAILY_DAYS) return [];
  return days.map((d) => {
    const t = sumRecords(queryMock(clientId, platform, { start: d, end: d }, 'campaign', today));
    return { date: d, spend: t.spend, conversions: t.conversions, leads: t.leads ?? null, purchases: t.purchases ?? null, conversion_value: t.conversion_value, clicks: t.clicks, impressions: t.impressions };
  });
}

function adsPeriod(p: PeriodData, clientId: string, platform: Platform, range: DateRange, today: string): AdsPeriod {
  return { totals: sumRecords(p.campaigns), campaigns: p.campaigns, adSets: p.adSets, daily: dailyMock(clientId, platform, range, today) };
}

/** El mock de GA4 usa el modelo común: sessions = clicks, users = reach, key events = conversions, revenue = value. */
function ga4FromRecords(records: MetricRecord[]): Ga4Row[] {
  return records.map((r) => ({
    name: r.campaign_name,
    users: r.reach,
    sessions: r.clicks,
    engaged_sessions: null,
    engagement_rate: null,
    key_events: r.conversions,
    revenue: r.conversion_value,
  }));
}

function ga4Period(p: PeriodData, clientId: string, range: DateRange, today: string): Ga4Period {
  const rows = ga4FromRecords(p.campaigns);
  const sum = (k: 'users' | 'sessions' | 'key_events' | 'revenue') => rows.reduce((a, r) => a + r[k], 0);
  const days = eachDay(range).filter((d) => d <= today);
  const daily =
    days.length > MAX_DAILY_DAYS
      ? []
      : days.map((d) => {
          const day = ga4FromRecords(queryMock(clientId, 'google_analytics', { start: d, end: d }, 'campaign', today));
          const s = (k: 'users' | 'sessions' | 'key_events' | 'revenue') => day.reduce((a, r) => a + r[k], 0);
          return { date: d, users: s('users'), sessions: s('sessions'), engaged_sessions: null, engagement_rate: null, key_events: s('key_events'), revenue: s('revenue') };
        });
  return {
    totals: { users: sum('users'), sessions: sum('sessions'), engaged_sessions: null, engagement_rate: null, key_events: sum('key_events'), revenue: sum('revenue') },
    daily,
    by_source_medium: rows,
    by_campaign: [],
    by_landing_page: [],
  };
}

const LABELS = { google_ads: 'Google Ads', meta_ads: 'Meta Ads', spotify_ads: 'Spotify Ads', ga4: 'Google Analytics' } as const;

function base<P>(client: ClientConfig, platform: keyof typeof LABELS, accountId: string | undefined): PlatformBlock<P> {
  return {
    platform,
    label: LABELS[platform],
    status: accountId ? 'ok' : 'not_linked',
    source: 'mock',
    accountId: accountId ?? null,
    currency: client.currency,
    message: accountId ? undefined : `${client.name} no tiene una cuenta mock de ${LABELS[platform]}.`,
    current: null,
    previous: null,
  };
}

async function safe<P>(block: PlatformBlock<P>, load: () => Promise<Pick<PlatformBlock<P>, 'current' | 'previous'> | null>): Promise<PlatformBlock<P>> {
  if (block.status !== 'ok') return block;
  try {
    const res = await load();
    return res ? { ...block, ...res } : { ...block, status: 'not_linked' };
  } catch (e) {
    return { ...block, status: 'error', message: `${block.label} temporalmente no disponible`, detail: e instanceof Error ? e.message : String(e) };
  }
}

export async function buildMockDashboard(client: ClientConfig, selection: DateSelection, today: string, signal?: AbortSignal): Promise<DashboardData> {
  const { range, compareRange } = selection;
  const ads = (platform: 'google_ads' | 'meta_ads') =>
    safe(base<AdsPeriod>(client, platform, client.sources[platform]?.accountId), async () => {
      const r = await fetchPlatformReport(client, platform, range, compareRange, today, signal);
      if (!r) return null;
      return {
        current: adsPeriod(r.current, client.id, platform, range, today),
        previous: r.previous && compareRange ? adsPeriod(r.previous, client.id, platform, compareRange, today) : null,
      };
    });
  const ga4 = safe(base<Ga4Period>(client, 'ga4', client.sources.google_analytics?.accountId), async () => {
    const r = await fetchPlatformReport(client, 'google_analytics', range, compareRange, today, signal);
    if (!r) return null;
    return {
      current: ga4Period(r.current, client.id, range, today),
      previous: r.previous && compareRange ? ga4Period(r.previous, client.id, compareRange, today) : null,
    };
  });
  const [google_ads, meta_ads, ga4Block] = await Promise.all([ads('google_ads'), ads('meta_ads'), ga4]);
  // No se fabrican datos mock de Spotify: la integración es exclusivamente real.
  const spotify_ads = base<AdsPeriod>(client, 'spotify_ads', undefined);
  return {
    source: 'mock',
    clientId: client.id,
    range,
    compareRange,
    generatedAt: new Date().toISOString(),
    google_ads,
    meta_ads,
    spotify_ads,
    ga4: ga4Block,
  };
}
