import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/backend';
import { formatValue, type FormatOptions } from '../lib/format';
import { aggregateVisibleDetailRows, searchDetailRows } from '../lib/performanceDetail';
import type {
  PerformanceDetailLevel, PerformanceDetailPlatform, PerformanceDetailRow,
  PerformanceDetailSpend,
} from '../types/reporting';
import type { DateRange } from '../types/report';
import { SectionTitle } from './common';
import { metaResultMode, valueMetricsEnabled } from '../config/valueMetrics';
import { BudgetManager } from '../components/BudgetManager';
import type { ApiClient } from '../types/dashboard';
import { detailStatus, filterDetailRows, type DetailStatusFilter } from '../lib/performanceDetailFilters';

interface Props {
  clientId: string;
  currency: string;
  locale: string;
  range: DateRange;
  refreshNonce: number;
  availablePlatforms: PerformanceDetailPlatform[];
  reportingClient: ApiClient | null;
}

type PlatformChoice = 'all' | PerformanceDetailPlatform;
const PLATFORM_LABEL: Record<PerformanceDetailPlatform, string> = {
  google_ads: 'Google Ads',
  meta_ads: 'Meta Ads',
  spotify_ads: 'Spotify Ads',
  pinterest_ads: 'Pinterest Ads',
};

const money = (value: number | null | undefined, fmt: FormatOptions) =>
  value == null ? '—' : formatValue(value, 'currency', fmt);
const number = (value: number | null | undefined, fmt: FormatOptions) =>
  value == null ? '—' : formatValue(value, 'number', fmt);
const decimal = (value: number | null | undefined, fmt: FormatOptions) =>
  value == null ? '—' : formatValue(value, 'decimal', fmt);
const percent = (value: number | null | undefined, fmt: FormatOptions) =>
  value == null ? '—' : formatValue(value, 'percent', fmt);
const ratio = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(2)}x`;

function groupsLabel(row: PerformanceDetailRow): string {
  const names = [...new Set((row.reporting_groups ?? []).map((group) => group.name))];
  return names.join(' · ');
}

function statusClass(row: PerformanceDetailRow): string {
  const current = row.effective_status || row.status;
  if (!current) return 'detail-status';
  if (current === 'ACTIVE' || current === 'ENABLED') return 'detail-status is-active';
  if (['PAUSED', 'REMOVED', 'ARCHIVED', 'CAMPAIGN_PAUSED', 'ADSET_PAUSED', 'WITH_ISSUES'].includes(current)) return 'detail-status is-inactive';
  return 'detail-status is-unknown';
}

function statusText(row: PerformanceDetailRow): string {
  const classification = detailStatus(row);
  if (classification === 'active') return 'Activo';
  if (classification === 'unknown') return 'Desconocido';
  return row.status_label || 'Inactivo';
}

export function PerformanceDetailSection({ clientId, currency, locale, range, refreshNonce, availablePlatforms, reportingClient }: Props) {
  const availablePlatformsKey = availablePlatforms.join(',');
  const [platform, setPlatform] = useState<PlatformChoice>('all');
  const [level, setLevel] = useState<PerformanceDetailLevel>('campaign');
  const [status, setStatus] = useState<DetailStatusFilter>('all');
  const [spend, setSpend] = useState<PerformanceDetailSpend>('all');
  const [query, setQuery] = useState('');
  const [groupFilter, setGroupFilter] = useState('all');
  const [rows, setRows] = useState<PerformanceDetailRow[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const lastRefreshNonce = useRef(refreshNonce);

  useEffect(() => {
    const ctrl = new AbortController();
    const force = lastRefreshNonce.current !== refreshNonce;
    lastRefreshNonce.current = refreshNonce;
    setLoading(true);
    setErrors([]);
    setRows([]);
    const selected = platform === 'all' ? availablePlatforms : availablePlatforms.includes(platform) ? [platform] : [];
    if (selected.length === 0) {
      setLoading(false);
      return () => ctrl.abort();
    }
    Promise.allSettled(selected.map((item) => api.performanceDetail(
      clientId, item, range.start, range.end, { level, status: 'all', spend: 'all' }, ctrl.signal, force,
    )))
      .then((results) => {
        if (ctrl.signal.aborted) return;
        const nextRows: PerformanceDetailRow[] = [];
        const nextErrors: string[] = [];
        results.forEach((result, index) => {
          if (result.status === 'fulfilled') nextRows.push(...result.value.rows);
          else {
            const detail = result.reason instanceof Error ? result.reason.message : String(result.reason);
            nextErrors.push(`${PLATFORM_LABEL[selected[index]]}: ${detail}`);
          }
        });
        setRows(nextRows);
        setErrors(nextErrors);
      })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [clientId, range.start, range.end, platform, level, refreshNonce, availablePlatformsKey]);

  const filteredRows = useMemo(() => filterDetailRows(rows, status, spend), [rows, status, spend]);
  const searchedRows = useMemo(() => searchDetailRows(filteredRows, query), [filteredRows, query]);
  const groupChoices = useMemo(() => [...new Map(rows.flatMap((row) => (row.reporting_groups ?? []).map((group) => [group.id ?? '', group.name] as const))).entries()], [rows]);
  const visibleRows = useMemo(() => searchedRows.filter((row) => groupFilter === 'all'
    || (groupFilter === 'unassigned' ? !(row.reporting_groups?.length) : row.reporting_groups?.some((group) => group.id === groupFilter))), [searchedRows, groupFilter]);
  const totals = useMemo(() => aggregateVisibleDetailRows(visibleRows), [visibleRows]);
  const metaTotals = useMemo(() => aggregateVisibleDetailRows(visibleRows.filter((row) => row.platform === 'meta_ads')), [visibleRows]);
  const googleTotals = useMemo(() => aggregateVisibleDetailRows(visibleRows.filter((row) => row.platform === 'google_ads')), [visibleRows]);
  const leadRows = visibleRows.filter((row) => row.platform === 'google_ads' || row.platform === 'meta_ads');
  const leadsTotal = leadRows.length && leadRows.every((row) => row.leads != null) ? leadRows.reduce((sum, row) => sum + (row.leads ?? 0), 0) : null;
  const leadCurrencies = new Set(leadRows.map((row) => row.currency).filter(Boolean));
  const leadSpend = leadRows.length && leadCurrencies.size <= 1 && leadRows.every((row) => row.spend != null)
    ? leadRows.reduce((sum, row) => sum + (row.spend ?? 0), 0) : null;
  const leadCpl = leadSpend != null && leadsTotal != null && leadsTotal !== 0 ? leadSpend / leadsTotal : null;
  const currencies = new Set(visibleRows.map((row) => row.currency).filter((value): value is string => Boolean(value)));
  const mixedCurrency = currencies.size > 1;
  const fmt: FormatOptions = { currency: currencies.size === 1 ? [...currencies][0] : currency, locale };
  const allGroupNames = visibleRows.some((row) => (row.reporting_groups?.length ?? 0) > 0);
  const label = level === 'campaign' ? 'Campaña' : 'Anuncio';
  const campaignKey = (row: PerformanceDetailRow) => `${row.platform}:${row.source_account_id}:${row.campaign_id}`;
  const allCampaignCount = new Set(rows.map(campaignKey)).size;
  const visibleCampaignCount = new Set(visibleRows.map(campaignKey)).size;

  const showMeta = platform === 'all' || platform === 'meta_ads';
  const showGoogle = platform === 'all' || platform === 'google_ads';
  const purchaseMode = metaResultMode(clientId) === 'purchase';
  const allowValue = valueMetricsEnabled(clientId) && visibleRows.some((row) => row.value_available !== false);
  const baseColumns = [
    { key: 'platform', title: 'Plataforma', render: (row: PerformanceDetailRow) => PLATFORM_LABEL[row.platform] },
    ...(allGroupNames ? [{ key: 'group', title: 'Grupo', render: groupsLabel }] : []),
    ...(level === 'ad' ? [
      { key: 'campaign', title: 'Campaña padre', render: (row: PerformanceDetailRow) => row.campaign_name || '—' },
      { key: 'parent', title: platform === 'google_ads' ? 'Ad group' : 'Ad group / ad set', render: (row: PerformanceDetailRow) => row.ad_group_name || row.ad_set_name || '—' },
      { key: 'ad', title: 'Anuncio', render: (row: PerformanceDetailRow) => row.ad_name || (row.ad_id ? `Anuncio ${row.ad_id}` : '—') },
    ] : [{ key: 'campaign', title: 'Nombre', render: (row: PerformanceDetailRow) => row.campaign_name || '—' }]),
    { key: 'status', title: 'Estado', render: (row: PerformanceDetailRow) => <span className={statusClass(row)} title={row.effective_status || row.status || undefined}>{statusText(row)}</span> },
    { key: 'spend', title: 'Gasto', render: (row: PerformanceDetailRow) => money(row.spend, { ...fmt, currency: row.currency || fmt.currency }), numeric: true },
    ...(showMeta && purchaseMode ? [
      { key: 'purchases', title: 'Compras', render: (row: PerformanceDetailRow) => row.platform === 'meta_ads' ? decimal(row.purchases, fmt) : '—', numeric: true },
      { key: 'cost_per_purchase', title: 'Costo por resultado', render: (row: PerformanceDetailRow) => row.platform === 'meta_ads' ? money(row.cost_per_purchase, { ...fmt, currency: row.currency || fmt.currency }) : '—', numeric: true },
    ] : []),
    ...(!purchaseMode && (showGoogle || showMeta) ? [
      { key: 'leads', title: 'Clientes potenciales', render: (row: PerformanceDetailRow) => decimal(row.leads, fmt), numeric: true },
    ] : []),
    ...(!purchaseMode && (showGoogle || showMeta) ? [
      { key: 'cpl', title: 'Costo por resultado', render: (row: PerformanceDetailRow) => row.platform === 'meta_ads' && purchaseMode ? '—' : money(row.cpl, { ...fmt, currency: row.currency || fmt.currency }), numeric: true },
    ] : []),
    ...(showGoogle ? [
      { key: 'conversions', title: 'Conversiones', render: (row: PerformanceDetailRow) => row.platform === 'google_ads' ? decimal(row.conversions, fmt) : '—', numeric: true },
      { key: 'cpa', title: 'CPA · Costo por adquisición', render: (row: PerformanceDetailRow) => row.platform === 'google_ads' ? money(row.cpa, { ...fmt, currency: row.currency || fmt.currency }) : '—', numeric: true },
    ] : []),
    { key: 'cpc', title: 'CPC', render: (row: PerformanceDetailRow) => money(row.cpc, { ...fmt, currency: row.currency || fmt.currency }), numeric: true },
    { key: 'impressions', title: 'Impresiones', render: (row: PerformanceDetailRow) => number(row.impressions, fmt), numeric: true },
    { key: 'clicks', title: 'Clics', render: (row: PerformanceDetailRow) => number(row.clicks, fmt), numeric: true },
    { key: 'ctr', title: 'CTR', render: (row: PerformanceDetailRow) => percent(row.ctr, fmt), numeric: true },
    ...(platform === 'meta_ads' ? [{ key: 'reach', title: 'Alcance', render: (row: PerformanceDetailRow) => number(row.reach, fmt), numeric: true }] : []),
    ...(allowValue ? [
      { key: 'value', title: 'Valor de conversión', render: (row: PerformanceDetailRow) => row.value_available === false ? '—' : money(row.conversion_value, { ...fmt, currency: row.currency || fmt.currency }), numeric: true },
      { key: 'roas', title: 'ROAS', render: (row: PerformanceDetailRow) => row.value_available === false ? '—' : ratio(row.roas), numeric: true },
    ] : []),
  ];
  const totalValue: Record<string, string> = {
    spend: money(totals.spend, fmt), impressions: number(totals.impressions, fmt),
    clicks: number(totals.clicks, fmt), conversions: decimal(googleTotals.conversions, fmt),
    leads: decimal(leadsTotal, fmt), cpl: money(leadCpl, fmt), cpc: money(totals.cpc, fmt),
    purchases: decimal(metaTotals.purchases, fmt), cost_per_purchase: money(metaTotals.cost_per_purchase, fmt),
    ctr: percent(totals.ctr, fmt), cpa: money(googleTotals.cpa, fmt),
    value: money(totals.conversion_value, fmt),
    roas: ratio(totals.roas), reach: number(metaTotals.reach, fmt),
  };

  return (
    <section id="performance-detail" className="report-section performance-detail" tabIndex={-1}>
      <SectionTitle title="Detalle de Performance" tag="tag-total" />
      <div className="card detail-controls detail-filter-grid">
        <fieldset className="detail-level-filter">
          <legend>Nivel</legend>
          <div className="detail-choice" role="group" aria-label="Nivel de detalle">
            {(['campaign', 'ad'] as const).map((value) => (
              <button key={value} type="button" aria-pressed={level === value} onClick={() => setLevel(value)}>
                {value === 'campaign' ? 'Campañas' : 'Anuncios'}
              </button>
            ))}
          </div>
        </fieldset>
        <label className="detail-platform-filter">Plataforma
          <select aria-label="Plataforma del detalle" value={platform} onChange={(event) => setPlatform(event.target.value as PlatformChoice)}>
            <option value="all">Todas</option>
            <option value="google_ads" disabled={!availablePlatforms.includes('google_ads')}>Google Ads</option>
            <option value="meta_ads" disabled={!availablePlatforms.includes('meta_ads')}>Meta Ads</option>
            <option value="spotify_ads" disabled={!availablePlatforms.includes('spotify_ads')}>Spotify Ads</option>
            <option value="pinterest_ads" disabled={!availablePlatforms.includes('pinterest_ads')}>Pinterest Ads</option>
          </select>
        </label>
        <label className="detail-status-filter">Estado
          <select aria-label="Filtrar por estado" value={status} onChange={(event) => setStatus(event.target.value as DetailStatusFilter)}>
            <option value="all">Todos</option><option value="active">Activos</option><option value="inactive">Inactivos</option><option value="unknown">Desconocidos</option>
          </select>
        </label>
        <label className="detail-spend-filter">Gasto del periodo
          <select aria-label="Filtrar por gasto" value={spend} onChange={(event) => setSpend(event.target.value as PerformanceDetailSpend)}>
            <option value="all">Todos</option><option value="with_spend">Con gasto</option><option value="without_spend">Sin gasto</option>
          </select>
        </label>
        <label className="detail-group-filter">Grupo
          <select aria-label="Filtrar por grupo" value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}>
            <option value="all">Todos</option><option value="unassigned">Sin grupo</option>{groupChoices.filter(([id]) => id).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>
        <label className="detail-search">Buscar campaña
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Nombre o ID de campaña" />
        </label>
      </div>
      {availablePlatforms.length === 0 ? <p className="card note">Este cliente no tiene fuentes publicitarias asignadas.</p> : <div className="table-card">
        <div className="table-head">
          <div>
            <h3>{visibleCampaignCount.toLocaleString(locale)} campañas visibles de {allCampaignCount.toLocaleString(locale)}</h3>
            <p className="muted small">Periodo {range.start} a {range.end}. Estado actual y gasto del periodo se filtran por separado.</p>
          </div>
        </div>
        {errors.map((error) => <p key={error} className="detail-error" role="alert">{error}</p>)}
        {mixedCurrency && <p className="note-inline small">Hay monedas distintas; los totales combinados no se muestran para evitar sumarlas sin conversión.</p>}
        {loading ? <p className="card state muted">Cargando detalle…</p> : visibleRows.length === 0
          ? <p className="card state muted">No hay {label.toLocaleLowerCase(locale)} para estos filtros.</p>
          : <div className="table-scroll detail-table-scroll"><table className="data-table detail-table">
              <thead><tr>{baseColumns.map((column) => <th key={column.key} className={'numeric' in column && column.numeric ? 'num' : undefined}>{column.title}</th>)}</tr></thead>
              <tbody>{visibleRows.map((row) => <tr key={`${row.platform}:${row.source_account_id}:${level === 'campaign' ? row.campaign_id : row.ad_id}`}>
                {baseColumns.map((column) => <td key={column.key} className={'numeric' in column && column.numeric ? 'num' : undefined}>{column.render(row)}</td>)}
              </tr>)}</tbody>
              <tfoot><tr>{baseColumns.map((column, index) => <td key={column.key} className={'numeric' in column && column.numeric ? 'num' : undefined}>{index === 0 ? 'TOTAL VISIBLE' : totalValue[column.key] ?? ''}</td>)}</tr></tfoot>
            </table></div>}
      </div>}
      {reportingClient && <BudgetManager client={reportingClient} range={range} filters={{
        platform,
        visibleCampaignKeys: [...new Set(visibleRows.map((row) => `${row.platform}:${row.source_account_id}:${row.campaign_id}`))],
      }} />}
    </section>
  );
}
