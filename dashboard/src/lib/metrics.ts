import type { MetricDirection } from '../config/metrics';
import type { BaseMetrics, MetricRecord } from '../types/metrics';

const safeDiv = (a: number, b: number): number => (b ? a / b : 0);

/** Ratio de presentación: ausencia o divisor cero no son un cero medido. */
export const safeRatio = (numerator: number | null | undefined, denominator: number | null | undefined): number | null =>
  numerator != null && denominator != null && denominator !== 0 && Number.isFinite(numerator) && Number.isFinite(denominator)
    ? numerator / denominator : null;

/** Recalcula métricas derivadas a partir de las base. Úsalo siempre tras agregar. */
export function deriveMetrics<T extends BaseMetrics>(m: T): T & Pick<MetricRecord, 'ctr' | 'cpc' | 'cpm' | 'cpa' | 'roas' | 'frequency' | 'conversion_rate'> {
  return {
    ...m,
    ctr: safeDiv(m.clicks, m.impressions),
    cpc: safeDiv(m.spend, m.clicks),
    cpm: safeDiv(m.spend, m.impressions) * 1000,
    cpa: safeDiv(m.spend, m.conversions),
    roas: safeDiv(m.conversion_value, m.spend),
    frequency: safeDiv(m.impressions, m.reach),
    conversion_rate: safeDiv(m.conversions, m.clicks),
  };
}

export type Totals = ReturnType<typeof deriveMetrics<BaseMetrics>> & {
  leads?: number | null;
  purchases?: number | null;
  cost_per_result?: number | null;
  cost_per_purchase?: number | null;
  value_available?: boolean;
  spend_available?: boolean;
  clicks_available?: boolean;
  conversions_available?: boolean;
  conversion_value_available?: boolean;
};

/**
 * Suma registros de un mismo periodo. El reach no es aditivo, así que el total
 * se deja en 0 (la UI lo muestra como "—") salvo que la API lo entregue a nivel cuenta.
 */
export function sumRecords(records: MetricRecord[]): Totals {
  const base: BaseMetrics = { impressions: 0, reach: 0, clicks: 0, spend: 0, conversions: 0, conversion_value: 0, inline_link_clicks: 0 };
  for (const r of records) {
    base.impressions += r.impressions;
    base.clicks += r.clicks;
    base.spend += r.spend;
    base.conversions += r.conversions;
    base.conversion_value += r.conversion_value;
    base.inline_link_clicks! += r.inline_link_clicks ?? 0;
  }
  base.spend = round2(base.spend);
  base.conversion_value = round2(base.conversion_value);
  const totals: Totals = deriveMetrics(base);
  const availableLeads = records.map((r) => r.leads).filter((n): n is number => n != null);
  totals.leads = records.length && availableLeads.length === records.length
    ? availableLeads.reduce((a, b) => a + b, 0) : null;
  totals.cost_per_result = safeRatio(totals.spend, totals.leads);
  const availablePurchases = records.map((r) => r.purchases).filter((n): n is number => n != null);
  totals.purchases = records.length && availablePurchases.length === records.length
    ? availablePurchases.reduce((a, b) => a + b, 0) : null;
  totals.cost_per_purchase = safeRatio(totals.spend, totals.purchases);
  totals.value_available = records.every((r) => r.value_available !== false);
  return totals;
}

export const round2 = (n: number) => Math.round(n * 100) / 100;

export type ChangeSentiment = 'positive' | 'negative' | 'neutral';

export interface Change {
  current: number;
  previous: number;
  absolute: number;
  /** Fracción (0.1291 = +12.91%). null si el valor anterior es 0. */
  percent: number | null;
  trend: 'up' | 'down' | 'flat';
  sentiment: ChangeSentiment;
}

export function computeChange(current: number, previous: number, direction: MetricDirection): Change {
  const absolute = current - previous;
  const percent = previous !== 0 ? absolute / Math.abs(previous) : null;
  const eps = 1e-9;
  const trend = Math.abs(absolute) < eps ? 'flat' : absolute > 0 ? 'up' : 'down';
  let sentiment: ChangeSentiment = 'neutral';
  if (direction !== 'neutral' && trend !== 'flat') {
    const improved = direction === 'higher' ? trend === 'up' : trend === 'down';
    sentiment = improved ? 'positive' : 'negative';
  }
  return { current, previous, absolute, percent, trend, sentiment };
}
