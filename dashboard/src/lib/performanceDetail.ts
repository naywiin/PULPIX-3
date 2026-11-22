import type { PerformanceDetailRow } from '../types/reporting';

const BASE = ['spend', 'impressions', 'clicks', 'conversions', 'purchases', 'leads', 'conversion_value', 'reach'] as const;

export interface VisibleDetailTotals {
  spend: number | null;
  impressions: number | null;
  clicks: number | null;
  conversions: number | null;
  purchases: number | null;
  leads: number | null;
  conversion_value: number | null;
  reach: number | null;
  cpc: number | null;
  ctr: number | null;
  cpa: number | null;
  cpl: number | null;
  cost_per_purchase: number | null;
  roas: number | null;
}

export function searchDetailRows(rows: PerformanceDetailRow[], query: string): PerformanceDetailRow[] {
  const q = query.trim().toLocaleLowerCase();
  if (!q) return rows;
  return rows.filter((row) => [
    row.campaign_name, row.campaign_id, row.ad_name, row.ad_id,
    row.ad_set_name, row.ad_group_name,
  ].some((value) => value != null && String(value).toLocaleLowerCase().includes(q)));
}

export function aggregateVisibleDetailRows(rows: PerformanceDetailRow[]) {
  const totals: Pick<VisibleDetailTotals, typeof BASE[number]> = {
    spend: null, impressions: null, clicks: null, conversions: null, purchases: null, leads: null, conversion_value: null, reach: null,
  };
  for (const field of BASE) {
    if (field === 'conversion_value' && rows.some((row) => row.value_available === false)) {
      totals[field] = null;
      continue;
    }
    const values = rows.map((row) => row[field]).filter((value): value is number => value !== null && value !== undefined);
    totals[field] = values.length ? values.reduce((sum, value) => sum + value, 0) : null;
  }
  const currencies = new Set(rows.map((row) => row.currency).filter(Boolean));
  if (currencies.size > 1) {
    totals.spend = null;
    totals.conversion_value = null;
  }
  const ratio = (
    numerator: keyof Pick<VisibleDetailTotals, typeof BASE[number]>,
    denominator: keyof Pick<VisibleDetailTotals, typeof BASE[number]>,
  ): number | null => {
    const a = totals[numerator];
    const b = totals[denominator];
    return a != null && b != null && b !== 0 ? a / b : null;
  };
  const result: VisibleDetailTotals = {
    ...totals,
    cpc: ratio('spend', 'clicks'),
    ctr: ratio('clicks', 'impressions'),
    cpa: ratio('spend', 'conversions'),
    cpl: ratio('spend', 'leads'),
    cost_per_purchase: ratio('spend', 'purchases'),
    roas: ratio('conversion_value', 'spend'),
  };
  return result;
}
