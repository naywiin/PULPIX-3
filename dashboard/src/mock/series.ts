import { addDays, diffDays, eachDay, parseISO } from '../lib/dates';
import type { DateRange } from '../types/report';
import { rand } from './random';

/**
 * Motor de series diarias mock.
 *
 * Cada entidad (campaña o ad set) tiene totales de referencia para un periodo
 * "ancla". Dentro del ancla los totales se reparten día a día de forma EXACTA
 * (la suma coincide al centavo con los valores de referencia). Fuera del ancla
 * se generan valores plausibles con estacionalidad, día de semana, tendencia
 * de crecimiento y ruido determinista, para que las comparaciones
 * (mes anterior, año anterior…) tengan sentido.
 */

export interface MockTotals {
  impressions: number;
  clicks: number;
  spend: number;
  conversions: number;
  conversion_value: number;
  /** Alcance único del periodo ancla (solo Meta). */
  reach?: number;
  inline_link_clicks?: number;
}

export interface DailyPoint {
  date: string;
  impressions: number;
  clicks: number;
  spend: number;
  conversions: number;
  conversion_value: number;
  inline_link_clicks: number;
}

export interface MockEntity {
  key: string;
  anchor: DateRange;
  totals: MockTotals;
  /** Primer día con datos (la entidad "no existía" antes). */
  activeFrom: string;
}

// Estacionalidad mensual (ene…dic): nov/dic más altos por temporada.
const SEASON = [0.86, 0.84, 0.92, 0.95, 1.0, 0.97, 0.99, 1.02, 1.0, 1.05, 1.18, 1.25];
// Día de semana (dom…sáb)
const WEEKDAY = [0.82, 1.02, 1.05, 1.06, 1.04, 1.0, 0.88];

function weekday(iso: string): number {
  const { y, m, d } = parseISO(iso);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

function dayWeight(key: string, iso: string): number {
  const { m } = parseISO(iso);
  return SEASON[m - 1] * WEEKDAY[weekday(iso)] * (0.75 + 0.5 * rand(`${key}|w|${iso}`));
}

/** Reparte un entero total en partes proporcionales a los pesos (método del mayor residuo). */
export function allocateInt(total: number, weights: number[]): number[] {
  const sum = weights.reduce((a, b) => a + b, 0);
  if (total === 0 || sum === 0) return weights.map(() => 0);
  const raw = weights.map((w) => (w / sum) * total);
  const out = raw.map(Math.floor);
  let rest = total - out.reduce((a, b) => a + b, 0);
  const order = raw.map((r, i) => [r - Math.floor(r), i] as const).sort((a, b) => b[0] - a[0]);
  for (let k = 0; rest > 0; k = (k + 1) % order.length, rest--) out[order[k][1]]++;
  return out;
}

const allocateCents = (total: number, weights: number[]) =>
  allocateInt(Math.round(total * 100), weights).map((c) => c / 100);

const cache = new Map<string, Map<string, DailyPoint>>();

function anchorSeries(e: MockEntity): Map<string, DailyPoint> {
  const cached = cache.get(e.key);
  if (cached) return cached;

  const days = eachDay(e.anchor);
  const w = days.map((d) => dayWeight(e.key, d));
  const convW = days.map((d, i) => w[i] * (0.6 + 0.8 * rand(`${e.key}|c|${d}`)));

  const impressions = allocateInt(e.totals.impressions, w);
  const clicks = allocateInt(e.totals.clicks, w);
  const spend = allocateCents(e.totals.spend, w);
  const conversions = allocateInt(e.totals.conversions, convW);
  const valueW = conversions.map((c, i) => c * (0.8 + 0.4 * rand(`${e.key}|v|${days[i]}`)));
  const value = allocateCents(e.totals.conversion_value, valueW);
  const ilc = allocateInt(e.totals.inline_link_clicks ?? 0, clicks);

  const map = new Map<string, DailyPoint>();
  days.forEach((date, i) =>
    map.set(date, {
      date,
      impressions: impressions[i],
      clicks: clicks[i],
      spend: spend[i],
      conversions: conversions[i],
      conversion_value: value[i],
      inline_link_clicks: ilc[i],
    }),
  );
  cache.set(e.key, map);
  return map;
}

function syntheticDay(e: MockEntity, date: string): DailyPoint {
  const days = eachDay(e.anchor);
  const sumW = days.reduce((acc, d) => acc + dayWeight(e.key, d), 0);
  const w = dayWeight(e.key, date);
  // Tendencia: la cuenta crece con el tiempo; hace un año tenía ~80–88% del volumen.
  const monthsFromAnchor = diffDays(e.anchor.start, date) / 30.4;
  const trend = Math.max(0.55, Math.min(1.15, 1 + 0.018 * monthsFromAnchor));
  const f = (w / sumW) * trend;
  const r = (tag: string) => rand(`${e.key}|${tag}|${date}`);

  const impressions = Math.round(e.totals.impressions * f);
  const clicks = Math.min(impressions, Math.round(e.totals.clicks * f * (0.9 + 0.2 * r('k'))));
  const spend = Math.round(e.totals.spend * f * (0.92 + 0.16 * r('s')) * 100) / 100;
  const expectedConv = e.totals.conversions * f * (0.6 + 0.8 * r('c'));
  const conversions = Math.floor(expectedConv + r('cr'));
  const avgValue = e.totals.conversions ? e.totals.conversion_value / e.totals.conversions : 0;
  const conversion_value = Math.round(conversions * avgValue * (0.75 + 0.5 * r('v')) * 100) / 100;
  const ilcRatio = e.totals.clicks ? (e.totals.inline_link_clicks ?? 0) / e.totals.clicks : 0;
  return { date, impressions, clicks, spend, conversions, conversion_value, inline_link_clicks: Math.round(clicks * ilcRatio) };
}

export function dailyPoint(e: MockEntity, date: string): DailyPoint | null {
  if (date < e.activeFrom) return null;
  if (date >= e.anchor.start && date <= e.anchor.end) return anchorSeries(e).get(date)!;
  return syntheticDay(e, date);
}

export interface EntityAggregate extends Omit<DailyPoint, 'date'> {
  reach: number;
}

/** Agrega una entidad en un rango (limitado a `maxDate`, normalmente hoy). */
export function aggregateEntity(e: MockEntity, range: DateRange, maxDate: string): EntityAggregate {
  const agg: EntityAggregate = { impressions: 0, clicks: 0, spend: 0, conversions: 0, conversion_value: 0, inline_link_clicks: 0, reach: 0 };
  const end = range.end < maxDate ? range.end : maxDate;
  for (let d = range.start; d <= end; d = addDays(d, 1)) {
    const p = dailyPoint(e, d);
    if (!p) continue;
    agg.impressions += p.impressions;
    agg.clicks += p.clicks;
    agg.spend += p.spend;
    agg.conversions += p.conversions;
    agg.conversion_value += p.conversion_value;
    agg.inline_link_clicks += p.inline_link_clicks;
  }
  agg.spend = Math.round(agg.spend * 100) / 100;
  agg.conversion_value = Math.round(agg.conversion_value * 100) / 100;
  // Reach no es aditivo: se estima con una curva de saturación respecto al ancla.
  if (e.totals.reach && e.totals.impressions && agg.impressions) {
    const est = e.totals.reach * Math.pow(agg.impressions / e.totals.impressions, 0.75);
    agg.reach = Math.min(agg.impressions, Math.round(est));
  }
  return agg;
}
