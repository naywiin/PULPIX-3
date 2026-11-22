import { useMemo, useState } from 'react';
import { EmptyState, ErrorState, KpiSkeleton } from '../components/States';
import { WOOSYNC_ALL_BRANCH_IDS, WOOSYNC_BRANCHES, WOOSYNC_DEFAULT_BRANCH } from '../config/woosync';
import { useWooSyncOverview } from '../hooks/useWooSyncOverview';
import { formatRange, resolveCompare } from '../lib/dates';
import { formatValue, type FormatOptions } from '../lib/format';
import { appliedFilters, comparisonOverview, isYearComparison, numeric, yearDelta } from '../lib/woosync';
import type { ClientConfig } from '../config/clients';
import type { DateSelection } from '../types/report';
import type { WooSyncCompare, WooSyncFilters, WooSyncOverview, WooSyncRow } from '../types/woosync';
import { SectionTitle } from './common';

interface SalesKpi { key: string; label: string; format: 'currency' | 'number'; aliases: readonly string[]; higherIsBetter?: boolean }

/** Resumen ejecutivo: los 9 KPIs visibles, en el orden de la pantalla (3 × 3). */
export const SALES_KPIS: readonly SalesKpi[] = [
  { key: 'total_sales', label: 'Ventas totales', format: 'currency', aliases: ['total_sales'] },
  { key: 'net_sales', label: 'Ventas netas', format: 'currency', aliases: ['net_sales'] },
  { key: 'gross_sales', label: 'Ventas brutas', format: 'currency', aliases: ['gross_sales'] },
  { key: 'orders_count', label: 'Órdenes', format: 'number', aliases: ['orders_count'] },
  { key: 'unique_customers_count', label: 'Clientes únicos', format: 'number', aliases: ['unique_customers_count'] },
  { key: 'average_order_value', label: 'Ticket promedio', format: 'currency', aliases: ['average_order_value'] },
  { key: 'items_sold', label: 'Productos vendidos', format: 'number', aliases: ['items_sold'] },
  { key: 'shipping_total', label: 'Total envío', format: 'currency', aliases: ['shipping_total', 'total_shipping'] },
  { key: 'coupon_amount', label: 'Cupones', format: 'currency', aliases: ['coupon_amount', 'coupon_total', 'coupons_total', 'coupons'] },
];

export function salesKpiValue(overview: WooSyncRow | null | undefined, kpi: SalesKpi): number | null {
  return numeric(overview, kpi.aliases);
}

export function SalesContent({ data, fmt }: { data: WooSyncOverview; fmt: FormatOptions }) {
  const previous = comparisonOverview(data);
  const showDelta = isYearComparison(data);
  const currentTotal = numeric(data.sales_overview, ['total_sales']);
  if (currentTotal === 0 && !numeric(data.sales_overview, ['orders_count']) && !showDelta) {
    return <EmptyState title="Sin ventas en el periodo" message="WooSync no devolvió actividad para los filtros seleccionados." />;
  }
  return <>
    <h3 className="sales-summary-title">Resumen ejecutivo</h3>
    <div className="kpi-grid sales-kpis">
      {SALES_KPIS.map((kpi) => {
        const current = salesKpiValue(data.sales_overview, kpi);
        const delta = showDelta ? yearDelta(current, salesKpiValue(previous, kpi), kpi.higherIsBetter ?? true) : null;
        return <div className="card kpi" key={kpi.key}>
          <div className="kpi-label">{kpi.label}</div>
          <div className="kpi-value">{formatValue(current, kpi.format, fmt)}</div>
          {delta && <div className="kpi-compare"><span className={`delta delta-${delta.tone}`}>{delta.label}</span></div>}
        </div>;
      })}
    </div>
  </>;
}

export function SalesResult({ status, data, error, refreshing, fmt, onRetry }: {
  status: 'idle' | 'loading' | 'ready' | 'error'; data: WooSyncOverview | null; error?: string; refreshing: boolean;
  fmt: FormatOptions; onRetry: () => void;
}) {
  if (status === 'loading') return <KpiSkeleton count={SALES_KPIS.length} className="sales-kpis" />;
  if (status === 'error') return <ErrorState message={error ?? 'WooSync no está disponible.'} onRetry={onRetry} />;
  if (!data) return <EmptyState title="Sin datos de WooSync" message="No se recibió información para los filtros seleccionados." />;
  return <div className={refreshing ? 'refreshing' : undefined} aria-busy={refreshing}><SalesContent data={data} fmt={fmt} /></div>;
}

interface Props { client: ClientConfig; selection: DateSelection; onSelectionChange: (selection: DateSelection) => void }

export interface WooSyncDraftFilters {
  from: string;
  to: string;
  branches: string[];
  compareTo: WooSyncCompare;
}

export function validateWooSyncDraftFilters(draft: WooSyncDraftFilters): { filters?: WooSyncFilters; error?: string } {
  if (!draft.from || !draft.to) return { error: 'Desde y Hasta son obligatorios.' };
  if (draft.from > draft.to) return { error: 'Desde debe ser anterior o igual a Hasta.' };
  return { filters: { from: draft.from, to: draft.to, branches: [...draft.branches], compareTo: draft.compareTo } };
}

export function sameFilters(left: WooSyncFilters, right: WooSyncFilters): boolean {
  return left.from === right.from
    && left.to === right.to
    && left.compareTo === right.compareTo
    && left.branches.join(',') === right.branches.join(',');
}

/** Cambiar de sucursal consulta al instante: conserva el periodo ya aplicado (las fechas en edición quedan pendientes). */
export function filtersForBranchChange(applied: WooSyncFilters, branches: string[]): WooSyncFilters {
  return { ...applied, branches: [...branches] };
}

/** El periodo en edición ya es el aplicado (no hay fechas pendientes de "Actualizar"). */
export function isPeriodApplied(applied: WooSyncFilters, draft: Pick<WooSyncDraftFilters, 'from' | 'to'>): boolean {
  return applied.from === draft.from && applied.to === draft.to;
}

/** Cambiar la comparación consulta al instante solo si el periodo está aplicado; si no, queda pendiente. */
export function filtersForCompareChange(applied: WooSyncFilters, draft: WooSyncDraftFilters, compareTo: WooSyncCompare): WooSyncFilters | null {
  return isPeriodApplied(applied, draft) ? { ...applied, compareTo } : null;
}

function AppliedFilterContext({ data }: { data: WooSyncOverview | null }) {
  const confirmed = appliedFilters(data);
  if (!data) return null;
  if (!confirmed) return <span className="muted small">Filtros aplicados por WooSync: no informados</span>;
  const allBranches = confirmed.branches.length === WOOSYNC_ALL_BRANCH_IDS.length
    && WOOSYNC_ALL_BRANCH_IDS.every((branch) => confirmed.branches.includes(branch));
  const branchLabels = confirmed.branches.map((id) => WOOSYNC_BRANCHES.find((branch) => branch.id === id)?.label ?? id);
  const parts: string[] = [];
  if (branchLabels.length) parts.push(allBranches ? `Todas las sedes (${branchLabels.join(', ')})` : branchLabels.join(', '));
  if (confirmed.from && confirmed.to) parts.push(formatRange({ start: confirmed.from, end: confirmed.to }, true));
  else if (confirmed.from) parts.push(`Desde ${confirmed.from}`);
  else if (confirmed.to) parts.push(`Hasta ${confirmed.to}`);
  const cmp = data.comparison;
  if (isYearComparison(data) && typeof cmp?.from === 'string' && typeof cmp.to === 'string') {
    parts.push(`Año anterior: ${formatRange({ start: cmp.from, end: cmp.to }, true)}`);
  }
  return <span className="muted small" data-applied-branches={confirmed.branches.join(',')}>
    Filtros aplicados por WooSync: {parts.length ? parts.join(' · ') : 'sin detalle'}
  </span>;
}

export function SalesSection({ client, selection, onSelectionChange }: Props) {
  const initialFilters: WooSyncFilters = {
    from: selection.range.start,
    to: selection.range.end,
    branches: [WOOSYNC_DEFAULT_BRANCH],
    compareTo: selection.compare === 'previous_year' ? 'previous_year' : 'none',
  };
  const [draftFilters, setDraftFilters] = useState<WooSyncDraftFilters>(() => ({ ...initialFilters, from: initialFilters.from!, to: initialFilters.to! }));
  const [appliedFiltersState, setAppliedFilters] = useState<WooSyncFilters>(initialFilters);
  const [requestVersion, setRequestVersion] = useState(0);
  const [validationError, setValidationError] = useState<string>();
  const filters = useMemo(() => appliedFiltersState, [appliedFiltersState]);
  const state = useWooSyncOverview(true, filters, requestVersion);
  const fmt: FormatOptions = { currency: 'MXN', locale: client.locale };
  const busy = state.status === 'loading' || state.refreshing;
  const branchValue = draftFilters.branches.length === 1 ? draftFilters.branches[0] : '__all__';
  const updateDraft = <K extends keyof WooSyncDraftFilters>(key: K, value: WooSyncDraftFilters[K]) => {
    setDraftFilters((current) => ({ ...current, [key]: value }));
    setValidationError(undefined);
  };
  const syncSelection = (next: WooSyncFilters) => {
    const range = { start: next.from!, end: next.to! };
    onSelectionChange({ ...selection, preset: 'custom', range, compare: next.compareTo, compareRange: resolveCompare(next.compareTo, range, null) });
  };
  const onBranchChange = (value: string) => {
    const branches = value === '__all__' ? [...WOOSYNC_ALL_BRANCH_IDS] : [value];
    updateDraft('branches', branches);
    // El cambio de estado de appliedFilters dispara la consulta a WooSync (sin pulsar Actualizar).
    setAppliedFilters((current) => filtersForBranchChange(current, branches));
  };
  const onCompareChange = (compareTo: WooSyncCompare) => {
    updateDraft('compareTo', compareTo);
    const next = filtersForCompareChange(appliedFiltersState, draftFilters, compareTo);
    if (!next) return; // fechas pendientes: se aplican con Actualizar
    setAppliedFilters(next);
    syncSelection(next);
  };
  const applyDraft = () => {
    const result = validateWooSyncDraftFilters(draftFilters);
    if (!result.filters) {
      setValidationError(result.error);
      return;
    }
    const next = result.filters;
    setValidationError(undefined);
    if (!sameFilters(appliedFiltersState, next)) setAppliedFilters(next);
    // Aun sin cambios de filtros, cada click produce una nueva consulta HTTP.
    setRequestVersion((version) => version + 1);
    syncSelection(next);
  };
  return <section className="report-section">
    <SectionTitle title="Ventas · WooSync" tag="tag-sales"><span className="muted small">Exclusivo de Ssiento</span>{busy && <span className="sales-loading" role="status">Actualizando…</span>}</SectionTitle>
    <div className="card sales-filters" aria-label="Filtros de ventas">
      <label>Sucursal<select className="input" value={branchValue} onChange={(event) => onBranchChange(event.target.value)}>{WOOSYNC_BRANCHES.map((branch) => <option key={branch.id} value={branch.id}>{branch.label}</option>)}<option value="__all__">Todas las sedes</option></select></label>
      <fieldset><legend>Periodo</legend><div className="sales-date-inputs"><label>Desde<input aria-label="Desde" type="date" className="input" value={draftFilters.from} onChange={(event) => updateDraft('from', event.target.value)} /></label><label>Hasta<input aria-label="Hasta" type="date" className="input" value={draftFilters.to} onChange={(event) => updateDraft('to', event.target.value)} /></label></div></fieldset>
      <label>Comparar<select className="input" value={draftFilters.compareTo} onChange={(event) => onCompareChange(event.target.value as WooSyncCompare)}><option value="none">Sin comparación</option><option value="previous_year">Año anterior</option></select></label>
      <button type="button" className="btn" onClick={applyDraft} disabled={busy}>⟳ Actualizar</button>
      {validationError && <div className="sales-filter-error" role="alert">{validationError}</div>}
    </div>
    <div className="sales-context"><AppliedFilterContext data={state.data} /></div>
    <SalesResult status={state.status} data={state.data} error={state.error} refreshing={state.refreshing} fmt={fmt} onRetry={state.retry} />
  </section>;
}
