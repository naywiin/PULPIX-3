import type { PerformanceDetailPlatform, PerformanceDetailRow } from '../types/reporting';
import type { DetailSpendFilter, DetailStatusFilter } from './performanceDetailFilters';
import { filterDetailRows } from './performanceDetailFilters';

export type MetaConversionMode = 'purchase' | 'lead';
export type ConversionMetricPlatform = PerformanceDetailPlatform;

export interface ConversionDailyPoint {
  date: string;
  conversions: number | null;
  spend: number | null;
}

export interface ConversionPlatformSummary {
  platform: ConversionMetricPlatform;
  available: boolean;
  currency: string | null;
  mixedCurrency: boolean;
  conversions: number | null;
  partialConversions: boolean;
  spend: number | null;
  daily: ConversionDailyPoint[];
  rows: PerformanceDetailRow[];
}

export interface ConversionAnalysis {
  google_ads: ConversionPlatformSummary;
  meta_ads: ConversionPlatformSummary;
  conversionsTotal: number | null;
  partialTotal: boolean;
  totalSpend: number | null;
  totalCurrency: string | null;
  mixedCurrency: boolean;
  cpa: number | null;
  cpaSources: ConversionMetricPlatform[];
  cpaPartial: boolean;
  visibleRows: PerformanceDetailRow[];
}

export interface AggregateConversionSource {
  available: boolean;
  currency: string | null;
  totals: { spend: number | null; conversions: number | null; purchases?: number | null; leads?: number | null };
  daily: { date: string; spend: number | null; conversions: number | null; purchases?: number | null; leads?: number | null }[];
}

/** Reconciles the unfiltered campaign view with provider-backed platform totals.
 * Campaign rows can omit zero-result actions; those null row values must not
 * erase an aggregate Purchase total that the same API reports explicitly.
 */
export function useAggregateConversionTotals(
  analysis: ConversionAnalysis,
  sources: Partial<Record<ConversionMetricPlatform, AggregateConversionSource>>,
  dates: string[],
  metaMode: MetaConversionMode,
): ConversionAnalysis {
  const summaries = (['google_ads', 'meta_ads'] as const).map((platform) => {
    const source = sources[platform];
    const previous = analysis[platform];
    if (!source?.available) return previous;
    const eventValue = platform === 'google_ads'
      ? source.totals.conversions
      : metaMode === 'purchase' ? source.totals.purchases : source.totals.leads;
    const eventIsValid = typeof eventValue === 'number' && Number.isFinite(eventValue);
    const dailyMap = new Map(source.daily.map((point) => [point.date, point]));
    const daily = dates.map((date) => {
      const point = dailyMap.get(date);
      const conversions = point == null ? null : platform === 'google_ads'
        ? point.conversions : metaMode === 'purchase' ? point.purchases ?? null : point.leads;
      return {
        date,
        conversions: typeof conversions === 'number' && Number.isFinite(conversions) ? conversions : null,
        spend: point?.spend != null && Number.isFinite(point.spend) ? point.spend : null,
      };
    });
    return {
      ...previous,
      available: true,
      currency: source.currency,
      conversions: eventIsValid ? eventValue : null,
      partialConversions: !eventIsValid,
      spend: source.totals.spend != null && Number.isFinite(source.totals.spend) ? source.totals.spend : null,
      daily,
    };
  });
  const [google_ads, meta_ads] = summaries;
  const selected = [google_ads, meta_ads].filter((item) => item.available);
  const known = selected.filter((item) => item.conversions != null);
  const conversionsTotal = known.length ? known.reduce((sum, item) => sum + (item.conversions ?? 0), 0) : null;
  const currencies = [...new Set(selected.map((item) => item.currency).filter((value): value is string => Boolean(value)))];
  const mixedCurrency = currencies.length > 1 || selected.some((item) => item.mixedCurrency);
  const totalCurrency = currencies.length === 1 && !selected.some((item) => item.mixedCurrency) ? currencies[0] : null;
  const spendComplete = selected.length > 0 && selected.every((item) => item.spend != null);
  const totalSpend = !mixedCurrency && spendComplete ? selected.reduce((sum, item) => sum + (item.spend ?? 0), 0) : null;
  const cpaCandidates = selected.filter((item) => item.conversions != null && item.spend != null);
  const cpaCurrencies = new Set(cpaCandidates.map((item) => item.currency).filter((value): value is string => Boolean(value)));
  const cpaSources = mixedCurrency || cpaCurrencies.size > 1 ? [] : cpaCandidates.map((item) => item.platform);
  const cpaSpend = cpaCandidates.reduce((sum, item) => sum + (item.spend ?? 0), 0);
  const cpaEvents = cpaCandidates.reduce((sum, item) => sum + (item.conversions ?? 0), 0);
  const cpa = cpaSources.length && cpaEvents !== 0 ? cpaSpend / cpaEvents : null;
  return {
    ...analysis,
    google_ads,
    meta_ads,
    conversionsTotal,
    partialTotal: selected.some((item) => item.conversions == null),
    totalSpend,
    totalCurrency,
    mixedCurrency,
    cpa,
    cpaSources,
    cpaPartial: cpaSources.length < selected.length || selected.some((item) => item.partialConversions),
  };
}

const metricFor = (row: PerformanceDetailRow, metaMode: MetaConversionMode): number | null => {
  if (row.platform === 'google_ads') return row.conversions;
  return metaMode === 'purchase' ? row.purchases ?? null : row.leads;
};

function summarizePlatform(
  platform: ConversionMetricPlatform,
  rows: PerformanceDetailRow[],
  sourceRows: PerformanceDetailRow[],
  dates: string[],
  metaMode: MetaConversionMode,
  available: boolean,
): ConversionPlatformSummary {
  // Currency belongs to the selected source, not just the rows surviving a
  // status/spend filter. Otherwise filtering out every row could hide a real
  // cross-platform currency mismatch and make an invalid combined CPA appear.
  const currencies = [...new Set(sourceRows.map((row) => row.currency).filter((value): value is string => Boolean(value)))];
  const mixedCurrency = currencies.length > 1;
  const metrics = rows.map((row) => metricFor(row, metaMode));
  const knownMetrics = metrics.filter((value): value is number => value != null);
  const conversions = !available ? null : rows.length === 0 ? 0
    : knownMetrics.length === 0 ? null : knownMetrics.reduce((sum, value) => sum + value, 0);
  const partialConversions = metrics.some((value) => value == null);
  const spend = !available || mixedCurrency || rows.some((row) => row.spend == null)
    ? null : rows.reduce((sum, row) => sum + (row.spend ?? 0), 0);
  const currency = currencies.length === 1 ? currencies[0] : null;
  const daily = dates.map((date) => {
    // A successful period response can omit daily rows for campaigns whose
    // measured period total is exactly zero. Only those rows can be filled
    // with daily zeroes; null metrics and positive spend without a daily
    // breakdown remain unavailable.
    const conversionsAvailable = available && rows.every((row) => Array.isArray(row.daily)
      || (row.spend === 0 && metricFor(row, metaMode) === 0));
    const spendAvailable = available && !mixedCurrency && rows.every((row) => row.spend != null
      && (Array.isArray(row.daily) || row.spend === 0));
    const points = rows.map((row) => row.daily?.find((point) => point.date === date));
    const values = rows.map((row, index) => {
      const point = points[index];
      if (!Array.isArray(row.daily)) return row.spend === 0 && metricFor(row, metaMode) === 0 ? 0 : null;
      if (metricFor(row, metaMode) == null) return null;
      if (!point) return 0;
      return row.platform === 'google_ads' ? point.conversions : (metaMode === 'purchase' ? point.purchases : point.leads);
    });
    return {
      date,
      conversions: !conversionsAvailable ? null : rows.length === 0 ? 0 : values.some((value) => value == null) ? null
        : values.reduce<number>((sum, value) => sum + (value ?? 0), 0),
      spend: !spendAvailable ? null : points.some((point) => point != null && point.spend == null)
        ? null : points.reduce<number>((sum, point) => sum + (point?.spend ?? 0), 0),
    };
  });
  return { platform, available, currency, mixedCurrency, conversions, partialConversions, spend, daily, rows };
}

export function analyzeConversions(
  rows: PerformanceDetailRow[],
  dates: string[],
  options: {
    status: DetailStatusFilter;
    spend: DetailSpendFilter;
    metaMode: MetaConversionMode;
    availablePlatforms: ConversionMetricPlatform[];
  },
): ConversionAnalysis {
  const visibleRows = filterDetailRows(rows, options.status, options.spend);
  const google_ads = summarizePlatform('google_ads', visibleRows.filter((row) => row.platform === 'google_ads'),
    rows.filter((row) => row.platform === 'google_ads'), dates, options.metaMode, options.availablePlatforms.includes('google_ads'));
  const meta_ads = summarizePlatform('meta_ads', visibleRows.filter((row) => row.platform === 'meta_ads'),
    rows.filter((row) => row.platform === 'meta_ads'), dates, options.metaMode, options.availablePlatforms.includes('meta_ads'));
  const selected = [google_ads, meta_ads].filter((item) => item.available);
  const knownConversions = selected.map((item) => item.conversions).filter((value): value is number => value != null);
  const conversionsTotal = knownConversions.length === 0 ? null : knownConversions.reduce((sum, value) => sum + value, 0);
  const partialTotal = selected.some((item) => item.conversions == null || item.partialConversions);
  const currencies = [...new Set(selected.map((item) => item.currency).filter((value): value is string => Boolean(value)))];
  const mixedCurrency = currencies.length > 1 || selected.some((item) => item.mixedCurrency);
  const totalCurrency = currencies.length === 1 && !selected.some((item) => item.mixedCurrency) ? currencies[0] : null;
  const totalSpend = mixedCurrency || selected.length === 0 || selected.some((item) => item.spend == null)
    ? null : selected.reduce((sum, item) => sum + (item.spend ?? 0), 0);
  // A partial CPA is valid only for platforms whose result AND spend are both
  // complete. Never divide total Google+Meta spend by results from Google only.
  const cpaCandidates = selected.filter((item) => !item.partialConversions && item.conversions != null && item.spend != null);
  const cpaCurrencies = [...new Set(cpaCandidates.map((item) => item.currency).filter((value): value is string => Boolean(value)))];
  const cpaSources = cpaCurrencies.length > 1 ? [] : cpaCandidates.map((item) => item.platform);
  const cpaSpend = cpaCandidates.reduce((sum, item) => sum + (item.spend ?? 0), 0);
  const cpaConversions = cpaCandidates.reduce((sum, item) => sum + (item.conversions ?? 0), 0);
  const cpa = cpaSources.length > 0 && cpaConversions > 0 ? cpaSpend / cpaConversions : null;
  const cpaPartial = cpaSources.length < selected.length || selected.some((item) => item.partialConversions);
  return { google_ads, meta_ads, conversionsTotal, partialTotal, totalSpend, totalCurrency, mixedCurrency, cpa,
    cpaSources, cpaPartial, visibleRows };
}
