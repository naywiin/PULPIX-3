import { addDays, daysInMonth, eachDay, toISO } from './dates';
import type { DateRange } from '../types/report';
import type { DailyAdsPoint } from '../types/dashboard';

export interface ProjectionSource {
  key: 'google_ads' | 'meta_ads';
  label: string;
  currency: string | null;
  available: boolean;
  daily: DailyAdsPoint[];
}

export interface ProjectionSeries { key: string; label: string; color: string; dash?: boolean; values: (number | null)[] }
export interface MonthlySalesProjection {
  key: string;
  label: string;
  start: string;
  end: string;
  dates: string[];
  daysInMonth: number;
  daysObserved: number;
  complete: boolean;
  closed: boolean;
  currency: string | null;
  observedBySource: Record<string, number | null>;
  projectedBySource: Record<string, number | null>;
  observedTotal: number | null;
  projectedTotal: number | null;
  totalPartial: boolean;
  mixedCurrency: boolean;
  series: ProjectionSeries[];
}

const MONTH_LABELS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
const COLORS = { google_ads: 'var(--series-gads)', meta_ads: 'var(--series-meta)' };

export function buildSalesProjections(
  range: DateRange,
  sources: ProjectionSource[],
  today: string,
): MonthlySalesProjection[] {
  const result: MonthlySalesProjection[] = [];
  let cursor = range.start.slice(0, 7);
  while (cursor <= range.end.slice(0, 7)) {
    const [year, month] = cursor.split('-').map(Number);
    const monthStart = toISO(year, month, 1);
    const monthEnd = toISO(year, month, daysInMonth(year, month));
    const start = range.start > monthStart ? range.start : monthStart;
    const selectedEnd = range.end < monthEnd ? range.end : monthEnd;
    const observedEnd = selectedEnd < today ? selectedEnd : today;
    const dates = eachDay({ start: monthStart, end: monthEnd });
    const days = daysInMonth(year, month);
    const closed = monthEnd < today;
    const selectedWholeMonth = start === monthStart && selectedEnd === monthEnd && observedEnd === monthEnd;
    const sourceData = sources.map((source) => {
      const dailyMap = new Map(source.daily.filter((point) => point.date >= start && point.date <= observedEnd).map((point) => [point.date, point.conversion_value]));
      const observedDays = [...dailyMap.values()].filter((value): value is number => value != null).length;
      const observed = source.available && observedDays > 0
        ? [...dailyMap.values()].reduce<number>((sum, value) => sum + (value ?? 0), 0)
        : null;
      const completed = selectedWholeMonth && source.available && observedDays === days;
      const projected = observed == null ? null : completed ? observed : closed ? null : observed / observedDays * days;
      let cumulative = 0;
      let lastObservedIndex = -1;
      const observedLine: (number | null)[] = dates.map((date, index) => {
        if (date < start || date > observedEnd) return null;
        const value = dailyMap.get(date);
        if (value == null) return null;
        cumulative += value;
        lastObservedIndex = index;
        return cumulative;
      });
      const forecastLine: (number | null)[] = dates.map(() => null);
      if (!completed && projected != null && observed != null && lastObservedIndex >= 0) {
        const remaining = days - lastObservedIndex - 1;
        for (let index = lastObservedIndex; index < days; index++) {
          const elapsed = index - lastObservedIndex;
          forecastLine[index] = remaining > 0 ? observed + ((projected - observed) * elapsed) / remaining : projected;
        }
      }
      return { source, observed, projected, observedLine, forecastLine, hasData: observed != null, completed, observedDays, lastObservedIndex };
    });
    const completed = selectedWholeMonth && sources.some((source) => source.available)
      && sourceData.filter((item) => item.source.available).every((item) => item.completed);
    const dataSources = sourceData.filter((item) => item.hasData);
    const availableSources = sources.filter((source) => source.available);
    const currencies = new Set(availableSources.map((item) => item.currency).filter((value): value is string => Boolean(value)));
    const currencyValid = currencies.size === 1 && availableSources.every((item) => item.currency != null);
    const observedTotal = currencyValid && dataSources.length ? dataSources.reduce((sum, item) => sum + (item.observed ?? 0), 0) : null;
    const projectedTotal = currencyValid && dataSources.length && dataSources.every((item) => item.projected != null)
      ? dataSources.reduce((sum, item) => sum + (item.projected ?? 0), 0) : null;
    const totalLine = dates.map((_, index) => {
      const values = dataSources.map((item) => item.observedLine[index]);
      return values.some((value) => value != null) ? values.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null;
    });
    const totalForecast = dates.map((_, index) => {
      const values = dataSources.map((item) => item.completed
        ? (index >= item.lastObservedIndex ? item.observed : null)
        : item.forecastLine[index]);
      return values.some((value) => value != null) ? values.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null;
    });
    const series: ProjectionSeries[] = [];
    for (const item of sourceData) {
      if (!item.hasData) continue;
      series.push({ key: `${item.source.key}-observed`, label: `${item.source.label} · observado`, color: COLORS[item.source.key], values: item.observedLine });
      if (!item.completed && item.projected != null) series.push({ key: `${item.source.key}-projection`, label: `${item.source.label} · proyección`, color: COLORS[item.source.key], dash: true, values: item.forecastLine });
    }
    if (currencyValid && dataSources.length) {
      series.push({ key: 'total-observed', label: `Total${dataSources.length < sources.length ? ' · parcial' : ''} · observado`, color: '#263445', values: totalLine });
      if (!completed && projectedTotal != null) series.push({ key: 'total-projection', label: 'Total · proyección', color: '#263445', dash: true, values: totalForecast });
    }
    result.push({
      key: cursor, label: `${MONTH_LABELS[month - 1]} ${year}`, start, end: selectedEnd, dates, daysInMonth: days,
      daysObserved: Math.max(0, ...sourceData.map((item) => item.observedDays)),
      complete: completed, closed, currency: currencyValid ? [...currencies][0] : (dataSources.length === 1 ? dataSources[0].source.currency : null),
      observedBySource: Object.fromEntries(sourceData.map((item) => [item.source.key, item.observed])),
      projectedBySource: Object.fromEntries(sourceData.map((item) => [item.source.key, item.projected])),
      observedTotal, projectedTotal, totalPartial: currencyValid && dataSources.length < sources.length,
      mixedCurrency: currencies.size > 1, series,
    });
    const next = addDays(monthEnd, 1);
    cursor = next.slice(0, 7);
  }
  return result;
}
