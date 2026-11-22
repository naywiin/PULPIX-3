import { DataTable, type Column } from '../components/DataTable';
import { LineChart } from '../components/LineChart';
import { formatRange } from '../lib/dates';
import { formatValue } from '../lib/format';
import type { AdsPeriod } from '../types/dashboard';
import type { MetricRecord } from '../types/metrics';
import type { DateSelection } from '../types/report';
import { SectionTitle, type BlockViewProps } from './common';
import type { ClientConfig } from '../config/clients';

interface Props extends BlockViewProps<AdsPeriod> { client: ClientConfig; selection: DateSelection }

const columns: Column<MetricRecord>[] = [
  { key: 'campaign_name', label: 'Campaña', value: (row) => row.campaign_name },
  { key: 'status', label: 'Estado', value: (row) => row.status ?? 'Desconocido' },
  { key: 'impressions', label: 'Impresiones', value: (row) => row.pinterestImpressions ?? null, format: 'number' },
  { key: 'clicks', label: 'Clics', value: (row) => row.pinterestClicks ?? null, format: 'number' },
  { key: 'ctr', label: 'CTR', value: (row) => row.pinterestCtr ?? null, format: 'percent' },
  { key: 'spend', label: 'Gasto', value: (row) => row.pinterestSpend ?? null, format: 'currency' },
  { key: 'checkouts', label: 'Checkouts', value: (row) => row.checkouts ?? null, format: 'number' },
  { key: 'cpa', label: 'CPA / costo por checkout', value: (row) => row.checkouts != null && row.pinterestSpend != null && row.checkouts > 0 ? row.pinterestSpend / row.checkouts : null, format: 'currency' },
  { key: 'conversion_value', label: 'Valor de conversión', value: (row) => row.pinterestConversionValue ?? null, format: 'currency' },
  { key: 'roas', label: 'ROAS', value: (row) => row.pinterestRoas ?? null, format: 'ratio' },
];

function MetricCard({ label, value, currency, kind = 'currency' }: { label: string; value: number | null | undefined; currency: string; kind?: 'currency' | 'number' | 'percent' | 'ratio' }) {
  return <article className="card kpi-card"><span className="muted small">{label}</span><strong>{value == null ? '—' : formatValue(value, kind, { currency, locale: 'es-MX' })}</strong></article>;
}

export function PinterestAdsSection({ client, selection, block, loading, onRetry }: Props) {
  const period = block?.current;
  const metrics = period?.pinterestTotals;
  const currency = metrics?.currency || block?.currency || client.currency;
  const statusMessage = block?.status === 'not_configured'
    ? 'Pinterest Ads no configurado para este cliente. Puedes asignar una fuente desde Settings → Clientes.'
    : block?.status === 'auth_error'
      ? 'Error de autenticación de Pinterest Ads.'
      : block?.status === 'permission_error'
        ? 'La cuenta no tiene permisos para consultar Pinterest Ads.'
        : block?.status === 'provider_error' || block?.status === 'error'
          ? 'No fue posible consultar Pinterest Ads.'
          : null;
  const points = period?.pinterestDaily ?? [];
  const body = loading
    ? <div className="card state muted">Cargando Pinterest Ads…</div>
    : statusMessage
      ? <div className="card state muted" role={block?.status?.endsWith('error') || block?.status === 'permission_error' ? 'alert' : undefined}>{statusMessage}{onRetry && <button className="btn btn-sm" onClick={onRetry}>Reintentar</button>}</div>
      : !period
        ? <div className="card state muted">No hay datos de Pinterest Ads para este periodo.</div>
        : <>
          {block?.status === 'partial' && <div className="card state state-warn" role="status">
            <strong>Datos parciales de Pinterest Ads.</strong>
            <span> Se muestra el resumen disponible; la serie diaria o algunas campañas no pudieron cargarse.</span>
            {block.warnings?.length ? <ul>{block.warnings.map((warning, index) => <li key={`${warning.accountId ?? 'pinterest'}-${index}`}>{warning.message}</li>)}</ul> : null}
            {onRetry && <button className="btn btn-sm" onClick={onRetry}>Reintentar</button>}
          </div>}
          <div className="kpi-grid pinterest-kpis">
            <MetricCard label="Gasto" value={metrics?.spend} currency={currency} />
            <MetricCard label="Impresiones" value={metrics?.impressions} currency={currency} kind="number" />
            <MetricCard label="Clics" value={metrics?.clicks} currency={currency} kind="number" />
            <MetricCard label="CTR" value={metrics?.ctr} currency={currency} kind="percent" />
            <MetricCard label="CPC" value={metrics?.cpc} currency={currency} />
            <MetricCard label="Checkouts" value={metrics?.checkouts} currency={currency} kind="number" />
            <MetricCard label="CPA / costo por checkout" value={metrics?.cost_per_checkout} currency={currency} />
            <MetricCard label="Valor de conversión" value={metrics?.conversion_value} currency={currency} />
            <MetricCard label="ROAS" value={metrics?.roas} currency={currency} kind="ratio" />
          </div>
          <LineChart title="Pinterest Ads · gasto diario" subtitle={formatRange(selection.range, true)} dates={points.map((point) => point.date)} series={[{ key: 'pinterest-spend', label: 'Pinterest Ads', color: '#bd081c', values: points.map((point) => point.spend) }]} format={(value) => formatValue(value, 'currency', { currency, locale: 'es-MX' })} />
          <DataTable title="Campañas Pinterest Ads" subtitle={formatRange(selection.range, true)} rows={period.campaigns} columns={columns} rowKey={(row) => `${row.account_id}:${row.campaign_id}`} fmt={{ currency, locale: 'es-MX' }} emptyMessage="No hay campañas con datos disponibles." />
        </>;
  return <section className="report-section"><SectionTitle title="Pinterest Ads" tag="tag-pinterest">{block?.accountId && <span className="muted small">Cuenta {block.accountId}</span>}</SectionTitle>{body}</section>;
}
