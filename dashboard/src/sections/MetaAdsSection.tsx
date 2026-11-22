import { DataTable, type Column } from '../components/DataTable';
import { LineChart } from '../components/LineChart';
import { KpiSkeleton, TableSkeleton } from '../components/States';
import type { ClientConfig } from '../config/clients';
import { METRICS } from '../config/metrics';
import { metaResultMode, type MetaResultMode } from '../config/valueMetrics';
import { filterHiddenColumns, visibleMetrics } from '../config/metricVisibility';
import { formatRange } from '../lib/dates';
import { formatValue, type FormatOptions } from '../lib/format';
import { sumRecords } from '../lib/metrics';
import type { AdsPeriod } from '../types/dashboard';
import type { MetricRecord } from '../types/metrics';
import type { DateSelection } from '../types/report';
import { BlockWarnings, KpiRow, SectionTitle, blockStatusView, type BlockViewProps } from './common';

const resultKpis = (mode: MetaResultMode) => mode === 'purchase'
  ? [METRICS.meta_purchases, METRICS.meta_cost_per_purchase]
  : [METRICS.meta_leads, METRICS.meta_cost_per_result];
const baseKpis = (mode: MetaResultMode) => [METRICS.meta_spend, ...resultKpis(mode),
  METRICS.cpc, METRICS.impressions, METRICS.clicks, METRICS.ctr, METRICS.meta_reach];
const PURCHASE_VALUE_KPIS = [METRICS.meta_conversion_value, METRICS.meta_roas];
const REACH_NOTE = 'El alcance sumado de varias cuentas no está deduplicado.';

interface Props extends BlockViewProps<AdsPeriod> {
  client: ClientConfig;
  selection: DateSelection;
  refreshing?: boolean;
  showTables?: boolean;
}

function columns(totals: ReturnType<typeof sumRecords>, reachLabel: string, mode: MetaResultMode): Column<MetricRecord>[] {
  const leadCost = (r: MetricRecord) => r.leads != null && r.leads !== 0 ? r.spend / r.leads : null;
  const purchaseCost = (r: MetricRecord) => r.purchases != null && r.purchases !== 0 ? r.spend / r.purchases : null;
  return [
    { key: 'campaign', label: 'Nombre', value: (r) => r.campaign_name,
      render: (r) => <>{r.campaign_name}{r.partial && <span className="src-tag">Campaña parcial</span>}
        {r.source_account_name && <span className="src-tag">{r.source_account_name}</span>}</> },
    { key: 'status', label: 'Estado', value: (r) => r.effective_status === 'ACTIVE' ? 'Activo' : r.effective_status ? 'Inactivo' : '—' },
    { key: 'spend', label: 'Gasto', value: (r) => r.spend, format: 'currency', total: totals.spend },
    ...(mode === 'purchase' ? [
      { key: 'purchases', label: 'Compras', value: (r: MetricRecord) => r.purchases ?? null, format: 'decimal' as const, total: totals.purchases ?? null },
      { key: 'cost_per_purchase', label: 'Costo por resultado', value: purchaseCost, format: 'currency' as const, total: totals.cost_per_purchase ?? null },
    ] : [
      { key: 'leads', label: 'Clientes potenciales', value: (r: MetricRecord) => r.leads ?? null, format: 'decimal' as const, total: totals.leads ?? null },
      { key: 'cost_per_result', label: 'Costo por resultado', value: leadCost, format: 'currency' as const, total: totals.cost_per_result ?? null },
    ]),
    { key: 'cpc', label: 'CPC', value: (r) => r.clicks ? r.spend / r.clicks : null, format: 'currency', total: totals.clicks ? totals.cpc : null },
    { key: 'impressions', label: 'Impresiones', value: (r) => r.impressions, format: 'number', total: totals.impressions },
    { key: 'clicks', label: 'Clics', value: (r) => r.clicks, format: 'number', total: totals.clicks },
    { key: 'ctr', label: 'CTR', value: (r) => r.impressions ? r.clicks / r.impressions : null, format: 'percent', total: totals.impressions ? totals.ctr : null },
    { key: 'reach', label: reachLabel, value: (r) => r.reach, format: 'number', total: totals.reach },
    ...(mode === 'purchase' ? [
      { key: 'conversion_value', label: 'Valor de conversión', value: (r: MetricRecord) => r.value_available === false ? null : r.conversion_value, format: 'currency' as const, total: totals.value_available === false ? null : totals.conversion_value },
      { key: 'roas', label: 'ROAS', value: (r: MetricRecord) => r.value_available === false ? null : r.spend ? r.conversion_value / r.spend : null, format: 'ratio' as const, total: totals.value_available === false || !totals.spend ? null : totals.roas },
    ] : []),
  ];
}

export function MetaAdsSection({ client, selection, block, loading, onRetry, refreshing, showTables = true }: Props) {
  const mode = metaResultMode(client.id);
  const fmt: FormatOptions = { currency: block?.currency ?? client.currency, locale: client.locale };
  const compareLabel = selection.compareRange ? formatRange(selection.compareRange) : undefined;
  const initialKpis = visibleMetrics(client, [...baseKpis(mode), ...(mode === 'purchase' ? PURCHASE_VALUE_KPIS : [])]);
  let body = blockStatusView({ block, loading, onRetry }, {
    clientName: client.name, isEmpty: (p) => p.campaigns.length === 0,
    skeleton: <><KpiSkeleton count={initialKpis.length} />{showTables && <TableSkeleton />}</>,
  });
  if (!body && block?.current) {
    const cur = block.current;
    const reachIsSum = (cur.sourcesCount ?? 1) > 1 && Boolean(cur.reachIsSum);
    const valueKpis = mode !== 'purchase' || cur.totals.value_available === false ? [] : PURCHASE_VALUE_KPIS.filter((metric) => metric.key !== 'roas' || cur.totals.spend > 0);
    const kpis = visibleMetrics(client, [...baseKpis(mode).slice(0, 7),
      reachIsSum ? METRICS.meta_reach_sum : METRICS.meta_reach, ...valueKpis]);
    const visibleColumns = (totals: ReturnType<typeof sumRecords>, reachLabel: string) =>
      filterHiddenColumns(client, columns(totals, reachLabel, mode).filter((column) =>
        totals.value_available !== false || (column.key !== 'conversion_value' && column.key !== 'roas')));
    const cols = visibleColumns(cur.totals, reachIsSum ? 'Alcance (suma de cuentas)' : 'Alcance');
    const adSetTotals = sumRecords(cur.adSets);
    const conversionCampaigns = cur.campaigns.filter((row) => row.objective === 'LEADS' || row.objective === 'SALES');
    const conversionTotals = sumRecords(conversionCampaigns);
    body = <div className={refreshing ? 'refreshing' : undefined}>
      <BlockWarnings warnings={block.warnings ?? []} noun="Cuenta" />
      <KpiRow metrics={kpis} current={cur.totals} previous={block.previous?.totals ?? null} fmt={fmt} compareLabel={compareLabel} />
      {reachIsSum && <p className="muted small note-inline">{REACH_NOTE}</p>}
      {showTables && <>
        <LineChart title="Gasto diario" subtitle={formatRange(selection.range, true)}
          dates={cur.daily.map((d) => d.date)}
          series={[{ key: 'meta', label: 'Gasto Meta Ads', color: 'var(--series-meta)', values: cur.daily.map((d) => d.spend) }]}
          format={(v) => formatValue(v, 'currency', fmt)} />
        <DataTable title="Campañas Meta Ads" subtitle={formatRange(selection.range, true)} rows={cur.campaigns}
          columns={cols} rowKey={(r) => r.campaign_id} fmt={fmt} initialSort={{ key: 'spend', dir: 'desc' }} />
        <DataTable title="Campañas con objetivo de conversión" subtitle={formatRange(selection.range, true)} rows={conversionCampaigns}
          columns={visibleColumns(conversionTotals, 'Alcance')}
          rowKey={(r) => r.campaign_id} fmt={fmt} initialSort={{ key: 'spend', dir: 'desc' }} />
        <DataTable title="Conjuntos de anuncios Meta Ads" subtitle={formatRange(selection.range, true)} rows={cur.adSets}
          columns={filterHiddenColumns(client, [
            { key: 'adset', label: 'Nombre', value: (r: MetricRecord) => r.ad_set_name ?? r.campaign_name },
            ...columns(adSetTotals, 'Alcance', mode).slice(1).filter((column) =>
              adSetTotals.value_available !== false || (column.key !== 'conversion_value' && column.key !== 'roas')),
          ])} rowKey={(r) => r.ad_set_id ?? r.campaign_id} fmt={fmt} initialSort={{ key: 'spend', dir: 'desc' }} />
      </>}
    </div>;
  }
  return <section className="report-section"><SectionTitle title="Meta Ads" tag="tag-meta" />{body}</section>;
}
