import { useEffect, useRef, useState } from 'react';
import { api } from '../api/backend';
import { BUDGET_PLATFORM_LABELS, BUDGETS_UPDATED_EVENT, MONTH_LABELS } from '../lib/reporting';
import { formatValue } from '../lib/format';
import type { DateRange } from '../types/report';
import type { PerformanceRecord, PerformanceSummary } from '../types/reporting';
import { SectionTitle } from './common';
import { metaResultMode, valueMetricsEnabled } from '../config/valueMetrics';

interface Props {
  clientId: string;
  currency: string;
  locale: string;
  range: DateRange;
  refreshNonce: number;
}

function money(value: number | null, rowCurrency: string | null, fallbackCurrency: string, locale: string) {
  if (value == null || !rowCurrency) return '—';
  return formatValue(value, 'currency', { currency: rowCurrency || fallbackCurrency, locale });
}

function pacingLabel(record: PerformanceRecord) {
  if (record.pacing_status === 'missing_budget') return 'Presupuesto no capturado';
  if (record.pacing_status === 'currency_mismatch') return 'Moneda incompatible';
  if (record.pacing_status === 'unavailable') return 'No disponible';
  return ({ green: 'En objetivo', yellow: 'Desviación moderada', red: 'Desviación alta' })[record.pacing_status];
}

const KPI_CELLS: { label: string; key: keyof PerformanceRecord; format: 'money' | 'number' | 'decimal' | 'percent' | 'ratio' }[] = [
  { label: 'Presupuesto mensual', key: 'monthly_budget', format: 'money' },
  { label: 'Presupuesto del periodo', key: 'budget_period', format: 'money' },
  { label: 'Gasto real', key: 'spend', format: 'money' },
  { label: 'Diferencia contra presupuesto', key: 'budget_difference', format: 'money' },
  { label: 'Gasto esperado', key: 'expected_spend', format: 'money' },
  { label: 'Diferencia de pacing', key: 'pacing_difference', format: 'money' },
  { label: '% presupuesto consumido', key: 'budget_consumed', format: 'percent' },
  { label: 'Conversiones', key: 'conversions', format: 'decimal' },
  { label: 'Clientes potenciales', key: 'leads', format: 'decimal' },
  { label: 'Compras', key: 'purchases', format: 'decimal' },
  { label: 'Valor de conversión', key: 'conversion_value', format: 'money' },
  { label: 'Promedio diario de conversiones', key: 'daily_conversions', format: 'decimal' },
  { label: 'Proyección fin de mes', key: 'projected_conversions', format: 'decimal' },
  { label: 'CPA · Costo por adquisición', key: 'cpa', format: 'money' },
  { label: 'Costo por resultado', key: 'cpl', format: 'money' },
  { label: 'Costo por resultado', key: 'cost_per_purchase', format: 'money' },
  { label: 'ROAS', key: 'roas', format: 'ratio' },
  { label: 'CPC', key: 'cpc', format: 'money' },
  { label: 'Clics', key: 'clicks', format: 'number' },
  { label: 'Impresiones', key: 'impressions', format: 'number' },
  { label: 'CTR', key: 'ctr', format: 'percent' },
  { label: 'Alcance', key: 'reach', format: 'number' },
  { label: 'Frecuencia', key: 'frequency', format: 'decimal' },
  { label: 'Ticket promedio', key: 'ticket_average', format: 'money' },
];

function display(record: PerformanceRecord, key: keyof PerformanceRecord, format: string, fallbackCurrency: string, locale: string) {
  if (record.platform === 'meta_ads' && ['conversions', 'cpa', 'daily_conversions', 'projected_conversions'].includes(key)) return '—';
  if (record.platform === 'meta_ads' && metaResultMode(record.client_id) === 'lead' && ['conversion_value', 'roas'].includes(key)) return '—';
  if (record.platform === 'google_ads' && ['purchases', 'cost_per_purchase', 'reach'].includes(key)) return '—';
  const value = record[key];
  if ((key === 'monthly_budget' || key === 'budget_period') && record.budget_captured && !record.budget_currency_compatible) return 'Moneda incompatible';
  if ((key === 'monthly_budget' || key === 'budget_period') && !record.budget_captured) return 'Presupuesto no capturado';
  if (format === 'money') return money(typeof value === 'number' ? value : null, record.currency, fallbackCurrency, locale);
  const kind = format as 'number' | 'decimal' | 'percent' | 'ratio';
  return formatValue(typeof value === 'number' ? value : null, kind, { currency: record.currency ?? fallbackCurrency, locale });
}

function SummaryCards({ record, currency, locale, clientId }: { record: PerformanceRecord; currency: string; locale: string; clientId: string }) {
  const purchaseMode = metaResultMode(clientId) === 'purchase';
  const cards = [
    ['Presupuesto mensual (suma)', 'monthly_budget', 'money'],
    ['Presupuesto proporcional', 'budget_period', 'money'],
    ['Gasto real', 'spend', 'money'],
    ['Diferencia vs. presupuesto', 'budget_difference', 'money'],
    ['Gasto esperado', 'expected_spend', 'money'],
    ['Diferencia de pacing', 'pacing_difference', 'money'],
    ['Conversiones totales', 'total_events', 'decimal'],
    ['CPA total', 'total_cpa', 'money'],
    ['CPC total', 'total_cpc', 'money'],
  ] as const;
  return <div className="pacing-summary-grid">
    {cards.map(([label, key, format]) => <article className="card pacing-kpi" key={key}>
      <span className="muted small">{key === 'total_events' && record.total_events_partial ? `${label} · parcial` : key === 'total_cpa' && record.total_cpa_partial ? `${label} · parcial` : label}</span>
      <strong>{key === 'total_events' ? record.total_events_available === false || record.total_events == null ? 'No disponible' : formatValue(record.total_events, 'decimal', { currency, locale })
        : key === 'total_cpa' ? money(record.total_cpa ?? null, record.currency, currency, locale)
          : key === 'total_cpc' ? money(record.total_cpc ?? null, record.currency, currency, locale)
            : display(record, key, format, currency, locale)}</strong>
      {key === 'total_events' && <span className="muted small">{(record.total_event_sources ?? []).map((source) => source === 'google_ads' ? 'Google: Conversiones' : source === 'meta_ads' ? `Meta: ${purchaseMode ? 'Compras' : 'Clientes potenciales'}` : source).join(' · ')} · eventos atribuidos; no personas únicas{record.total_events_partial ? '. Total parcial: hay fuentes sin resultado disponible.' : '.'}</span>}
      {key === 'monthly_budget' && record.budget_source && record.budget_source !== 'unavailable' && <span className="muted small">Origen: {record.budget_source === 'group' ? 'grupos' : record.budget_source === 'campaign' ? 'campañas' : 'grupos y campañas'}{record.budget_partial ? ' · suma parcial' : ''}</span>}
      {key === 'total_cpa' && record.total_cpa_partial && <span className="muted small">Incluye únicamente: {(record.total_cpa_sources ?? []).map((source) => source === 'google_ads' ? 'Google Ads' : source === 'meta_ads' ? 'Meta Ads' : source).join(', ') || 'fuentes disponibles'}.</span>}
      {key === 'total_cpa' && !record.total_cpa_partial && record.total_cpa == null && <span className="muted small">No se calcula si falta un resultado, hay monedas distintas o el divisor es cero.</span>}
      {key === 'pacing_difference' && <span className={`pacing-badge pacing-${record.pacing_status}`}>{pacingLabel(record)}</span>}
    </article>)}
  </div>;
}

export function pacingCells(clientId: string, rows: (Pick<PerformanceRecord, 'platform'> & Partial<Pick<PerformanceRecord, 'leads'>>)[]) {
  const meta = rows.some((row) => row.platform === 'meta_ads');
  const google = rows.some((row) => row.platform === 'google_ads');
  const purchaseMode = metaResultMode(clientId) === 'purchase';
  return KPI_CELLS.filter((cell) => {
    if ((cell.key === 'conversion_value' || cell.key === 'roas') && !valueMetricsEnabled(clientId)) return false;
    if (cell.key === 'ticket_average' && (!valueMetricsEnabled(clientId) || meta)) return false;
    if (cell.key === 'leads' && (purchaseMode || !((meta && !purchaseMode) || rows.some((row) => row.leads != null)))) return false;
    if (cell.key === 'cpl' && (purchaseMode || !((meta && !purchaseMode) || rows.some((row) => row.platform === 'google_ads' && row.leads != null)))) return false;
    if ((cell.key === 'purchases' || cell.key === 'cost_per_purchase') && (!meta || !purchaseMode)) return false;
    if ((cell.key === 'conversions' || cell.key === 'cpa' || cell.key === 'daily_conversions' || cell.key === 'projected_conversions') && !google) return false;
    if (cell.key === 'reach' && !meta) return false;
    return true;
  });
}

function MonthlyTable({ rows, currency, locale, clientId }: { rows: PerformanceRecord[]; currency: string; locale: string; clientId: string }) {
  const cells = pacingCells(clientId, rows);
  return <div className="table-scroll pacing-table-wrap"><table className="data-table pacing-table">
    <thead><tr><th>Mes</th><th>Medio</th>{cells.map((metric) => <th className="num" key={metric.key}>{metric.label}</th>)}<th>Estado de pacing</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={`${row.year}-${row.month}-${row.platform}`}>
      <td>{MONTH_LABELS[(row.month ?? 1) - 1]} {row.year}</td>
      <td>{row.platform ? BUDGET_PLATFORM_LABELS[row.platform] : '—'}</td>
      {cells.map((metric) => <td className="num" key={metric.key}>{display(row, metric.key, metric.format, currency, locale)}</td>)}
      <td><span className={`pacing-badge pacing-${row.pacing_status}`}>{pacingLabel(row)}</span></td>
    </tr>)}</tbody>
  </table></div>;
}

export function PerformancePacingSection({ clientId, currency, locale, range, refreshNonce }: Props) {
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [retry, setRetry] = useState(0);
  const previousRefreshNonce = useRef(refreshNonce);

  useEffect(() => {
    const controller = new AbortController();
    const forceRefresh = previousRefreshNonce.current !== refreshNonce;
    previousRefreshNonce.current = refreshNonce;
    const load = () => {
      setLoading(true);
      setError(undefined);
      api.performanceSummary(clientId, range, forceRefresh, controller.signal)
        .then((result) => {
          if (!controller.signal.aborted) setSummary(result);
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) {
            setSummary(null);
            setError(reason instanceof Error ? reason.message : String(reason));
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    };
    load();
    window.addEventListener(BUDGETS_UPDATED_EVENT, load);
    return () => {
      controller.abort();
      window.removeEventListener(BUDGETS_UPDATED_EVENT, load);
    };
  }, [clientId, range.start, range.end, refreshNonce, retry]);

  const rows = summary?.monthly ?? [];
  const total = summary?.period_total;
  return <section className="report-section performance-pacing">
    <SectionTitle title="Performance y Pacing" tag="tag-total" />
    <p className="muted small pacing-period">{range.start} → {range.end} · El resumen utiliza las plataformas asignadas al cliente.</p>
    {loading && !summary ? <div className="card state muted">Calculando KPI de performance…</div> : null}
    {error && <div className="card pacing-error" role="alert"><span>{error}</span><button type="button" className="btn" onClick={() => setRetry((value) => value + 1)}>Reintentar</button></div>}
    {total && <>
      <SummaryCards record={total} currency={currency} locale={locale} clientId={clientId} />
      <div className="card pacing-efficiency-grid">
        {(['budget_consumed', 'cpa', 'cpl', 'cost_per_purchase', 'roas', 'cpc', 'ctr'] as const)
          .filter((key) => pacingCells(clientId, rows).some((item) => item.key === key))
          .filter((key) => !(['cpa', 'cpl', 'cost_per_purchase'] as string[]).includes(key) || new Set(rows.map((row) => row.platform)).size === 1)
          .map((key) => {
          const metric = KPI_CELLS.find((item) => item.key === key)!;
          const partial = key === 'roas' && total.roas_partial;
          return <div key={key}><span className="muted small">{partial ? `${metric.label} · parcial` : metric.label}</span><strong>{display(total, key, metric.format, currency, locale)}</strong>{partial && <span className="muted small">Incluye: {(total.roas_sources ?? []).map((source) => source === 'google_ads' ? 'Google Ads' : source === 'meta_ads' ? 'Meta Ads' : source).join(', ')}. Falta valor compatible de otra plataforma.</span>}</div>;
        })}
      </div>
      <div className="card pacing-efficiency-grid pacing-secondary-kpis">
        {(['conversion_value', 'daily_conversions', 'clicks', 'impressions', 'reach', 'frequency', 'ticket_average'] as const)
          .filter((key) => pacingCells(clientId, rows).some((item) => item.key === key))
          .filter((key) => key !== 'daily_conversions' || rows.every((row) => row.platform === 'google_ads'))
          .map((key) => {
          const metric = KPI_CELLS.find((item) => item.key === key)!;
          const partial = key === 'conversion_value' && total.conversion_value_partial;
          return <div key={key}><span className="muted small">{partial ? `${metric.label} · parcial` : metric.label}</span><strong>{display(total, key, metric.format, currency, locale)}</strong>{partial && <span className="muted small">Incluye: {(total.conversion_value_sources ?? []).map((source) => source === 'google_ads' ? 'Google Ads' : source === 'meta_ads' ? 'Meta Ads' : source).join(', ')}. El total incluye únicamente valores disponibles de resultados compatibles.</span>}</div>;
        })}
      </div>
    </>}
    {rows.length > 0 && <>
      <h3 className="pacing-subtitle">{summary?.grouping_active ? 'Desglose por grupo, mes y plataforma' : 'Desglose mensual por plataforma'}</h3>
      {summary?.grouping_active ? [...new Set(rows.map((row) => row.reporting_group_id ?? '__general__'))].map((groupId) => {
        const groupRows = rows.filter((row) => (row.reporting_group_id ?? '__general__') === groupId);
        const label = groupId === '__general__' ? 'General / sin grupo' : groupRows[0]?.reporting_group_name ?? groupId;
        return <details className="card pacing-group-details" key={groupId} open><summary>{label}</summary><MonthlyTable rows={groupRows} currency={currency} locale={locale} clientId={clientId} /></details>;
      }) : <MonthlyTable rows={rows} currency={currency} locale={locale} clientId={clientId} />}
      {rows.some((row) => row.reach != null) && summary?.period_total?.reach_is_unique === false && <p className="muted small">El alcance de filas agregadas no se presenta como personas únicas: la fuente no deduplica cuentas, campañas ni periodos.</p>}
      {summary?.period_total?.currency == null && <p className="muted small">El total combina monedas distintas, por lo que se muestran KPI por plataforma y no se suman importes.</p>}
    </>}
    {!loading && !error && rows.length === 0 && <div className="card state muted">No hay plataformas publicitarias asignadas para este cliente.</div>}
  </section>;
}
