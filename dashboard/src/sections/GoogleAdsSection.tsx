import { DataTable, type Column } from '../components/DataTable';
import { LineChart } from '../components/LineChart';
import { KpiSkeleton, TableSkeleton } from '../components/States';
import type { ClientConfig } from '../config/clients';
import { METRICS } from '../config/metrics';
import { filterHiddenColumns, visibleMetrics } from '../config/metricVisibility';
import { formatRange } from '../lib/dates';
import { formatValue, type FormatOptions } from '../lib/format';
import type { AdsPeriod } from '../types/dashboard';
import type { MetricRecord } from '../types/metrics';
import type { DateSelection } from '../types/report';
import { BlockWarnings, KpiRow, SectionTitle, blockStatusView, type BlockViewProps } from './common';

const ALL_KPIS = [METRICS.gads_cost, METRICS.gads_conversions, METRICS.gads_cpa, METRICS.gads_avg_cpc,
  METRICS.impressions, METRICS.clicks, METRICS.ctr, METRICS.conversion_rate,
  METRICS.gads_conv_value, METRICS.gads_roas];

interface Props extends BlockViewProps<AdsPeriod> {
  client: ClientConfig;
  selection: DateSelection;
  refreshing?: boolean;
  showTables?: boolean;
}

export function GoogleAdsSection({ client, selection, block, loading, onRetry, refreshing, showTables = true }: Props) {
  const fmt: FormatOptions = { currency: block?.currency ?? client.currency, locale: client.locale };
  const compareLabel = selection.compareRange ? formatRange(selection.compareRange) : undefined;
  const kpis = visibleMetrics(client, ALL_KPIS);
  let body = blockStatusView({ block, loading, onRetry }, {
    clientName: client.name, isEmpty: (p) => p.campaigns.length === 0,
    skeleton: <><KpiSkeleton count={kpis.length} />{showTables && <TableSkeleton />}</>,
  });
  if (!body && block?.current) {
    const cur = block.current;
    const totals = cur.totals;
    const currentKpis = kpis.filter((metric) => metric.key !== 'roas' || totals.spend > 0);
    const columns: Column<MetricRecord>[] = filterHiddenColumns(client, [
      { key: 'campaign', label: 'Nombre', value: (r) => r.campaign_name },
      { key: 'status', label: 'Estado', value: (r) => r.status === 'ENABLED' ? 'Activo' : r.status ? 'Inactivo' : '—' },
      { key: 'spend', label: 'Gasto', value: (r) => r.spend, format: 'currency', total: totals.spend },
      { key: 'conversions', label: 'Conversiones', value: (r) => r.conversions, format: 'decimal', total: totals.conversions },
      { key: 'cpa', label: 'CPA · Costo por adquisición', value: (r) => r.conversions ? r.spend / r.conversions : null, format: 'currency', total: totals.conversions ? totals.cpa : null },
      { key: 'cpc', label: 'CPC', value: (r) => r.clicks ? r.spend / r.clicks : null, format: 'currency', total: totals.clicks ? totals.cpc : null },
      { key: 'impressions', label: 'Impresiones', value: (r) => r.impressions, format: 'number', total: totals.impressions },
      { key: 'clicks', label: 'Clics', value: (r) => r.clicks, format: 'number', total: totals.clicks },
      { key: 'ctr', label: 'CTR', value: (r) => r.impressions ? r.clicks / r.impressions : null, format: 'percent', total: totals.impressions ? totals.ctr : null },
      { key: 'conversion_rate', label: 'Conversion Rate', value: (r) => r.clicks ? r.conversions / r.clicks : null, format: 'percent', total: totals.clicks ? totals.conversion_rate : null },
      { key: 'conversion_value', label: 'Valor de conversión', value: (r) => r.conversion_value, format: 'currency', total: totals.conversion_value },
      { key: 'roas', label: 'ROAS', value: (r) => r.spend ? r.conversion_value / r.spend : null, format: 'ratio', total: totals.spend ? totals.roas : null },
    ]);
    body = <div className={refreshing ? 'refreshing' : undefined}>
      <BlockWarnings warnings={block.warnings ?? []} noun="Cuenta" />
      <KpiRow metrics={currentKpis} current={totals} previous={block.previous?.totals ?? null} fmt={fmt} compareLabel={compareLabel} />
      {showTables && <>
        <LineChart title="Gasto diario" subtitle={formatRange(selection.range, true)} dates={cur.daily.map((d) => d.date)}
          series={[{ key: 'gads', label: 'Gasto Google Ads', color: 'var(--series-gads)', values: cur.daily.map((d) => d.spend) }]}
          format={(v) => formatValue(v, 'currency', fmt)} />
        <DataTable title="Campañas Google Ads" subtitle={formatRange(selection.range, true)} rows={cur.campaigns}
          columns={columns} rowKey={(r) => r.campaign_id} fmt={fmt} initialSort={{ key: 'spend', dir: 'desc' }} />
      </>}
    </div>;
  }
  return <section className="report-section"><SectionTitle title="Google Ads" tag="tag-gads" />{body}</section>;
}
