import type { WooSyncFilters, WooSyncOverview, WooSyncRow } from '../types/woosync';

/** top_limit ya no alimenta ninguna vista: el backend aplica su valor por defecto. */
export function buildWooSyncQuery(filters: WooSyncFilters): string {
  const query = new URLSearchParams();
  if (filters.from) query.set('from', filters.from);
  if (filters.to) query.set('to', filters.to);
  if (filters.branches.length) query.set('branches', filters.branches.join(','));
  if (filters.compareTo !== 'none') query.set('compare_to', filters.compareTo);
  return query.toString();
}

export function numeric(row: WooSyncRow | null | undefined, keys: readonly string[]): number | null {
  if (!row) return null;
  for (const key of keys) {
    const value = row[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return null;
}

export function text(row: WooSyncRow | null | undefined, keys: readonly string[]): string | null {
  if (!row) return null;
  for (const key of keys) {
    const value = row[key];
    if ((typeof value === 'string' || typeof value === 'number') && String(value).trim()) return String(value);
  }
  return null;
}

export interface NormalizedWooSyncAppliedFilters {
  branches: string[];
  from: string | null;
  to: string | null;
}

/** Lee exclusivamente los filtros que WooSync confirma en su respuesta. */
export function appliedFilters(data: WooSyncOverview | null): NormalizedWooSyncAppliedFilters | null {
  const raw = data?.applied_filters;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const rawBranches = raw.branches;
  const branches = Array.isArray(rawBranches)
    ? rawBranches.map(String).map((value) => value.trim().toLowerCase()).filter(Boolean)
    : typeof rawBranches === 'string'
      ? rawBranches.split(',').map((value) => value.trim().toLowerCase()).filter(Boolean)
      : [];
  return { branches, from: text(raw, ['from', 'from_date', 'date_from']), to: text(raw, ['to', 'to_date', 'date_to']) };
}

/** Comparación anual construida por el backend; solo se usa si está habilitada. */
export function isYearComparison(data: WooSyncOverview | null): boolean {
  return data?.comparison?.enabled === true && data.comparison.type === 'previous_year';
}

export function comparisonOverview(data: WooSyncOverview): WooSyncRow | null {
  if (!isYearComparison(data)) return null;
  const candidate = data.comparison?.sales_overview;
  return candidate && typeof candidate === 'object' && !Array.isArray(candidate) ? (candidate as WooSyncRow) : null;
}

/** ((actual - anterior) / anterior) * 100; null si no se puede calcular con seguridad. */
export function percentChange(current: number | null | undefined, previous: number | null | undefined): number | null {
  if (current == null || previous == null || !Number.isFinite(current) || !Number.isFinite(previous) || previous === 0) return null;
  const value = ((current - previous) / Math.abs(previous)) * 100;
  return Number.isFinite(value) ? value : null;
}

export type YearDeltaTone = 'positive' | 'negative' | 'neutral';
export interface YearDelta { label: string; tone: YearDeltaTone }

/** Texto seguro para la comparación por KPI; nunca produce Infinity ni NaN. */
export function yearDelta(current: number | null, previous: number | null, higherIsBetter = true): YearDelta {
  const pct = percentChange(current, previous);
  if (pct == null) {
    // 0 vs 0: sin cambio real. Cualquier otro caso sin base comparable es N/A.
    if (current === 0 && previous === 0) return { label: '0.0% vs año anterior', tone: 'neutral' };
    return { label: 'N/A vs año anterior', tone: 'neutral' };
  }
  const rounded = Math.round(pct * 10) / 10;
  const sign = rounded > 0 ? '+' : '';
  const tone: YearDeltaTone = rounded === 0 ? 'neutral' : (rounded > 0) === higherIsBetter ? 'positive' : 'negative';
  return { label: `${sign}${rounded.toFixed(1)}% vs año anterior`, tone };
}
