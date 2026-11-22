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

const ALL_KPIS = [
  METRICS.spotify_spend,
  METRICS.spotify_impressions,
  METRICS.spotify_clicks,
  METRICS.spotify_reach,
  METRICS.spotify_frequency,
  METRICS.spotify_ctr,
  METRICS.spotify_cpm,
];

interface Props extends BlockViewProps<AdsPeriod> {
  client: ClientConfig;
  selection: DateSelection;
  refreshing?: boolean;
  showTables?: boolean;
}

const performanceColumns = (name: 'campaign' | 'adset' | 'ad'): Column<MetricRecord>[] => [
  {
    key: name,
    label: name === 'campaign' ? 'Campaign' : name === 'adset' ? 'Ad Set' : 'Ad',
    value: (r) => name === 'campaign' ? r.campaign_name : name === 'adset' ? (r.ad_set_name ?? '') : (r.ad_name ?? ''),
    render: (r) => (
      <>
        <span className="block">{name === 'campaign' ? r.campaign_name : name === 'adset' ? r.ad_set_name : r.ad_name}</span>
        {name !== 'campaign' && <span className="muted small block">{r.campaign_name}</span>}
      </>
    ),
  },
  { key: 'impressions', label: 'Impressions', value: (r) => r.impressions, format: 'number' },
  { key: 'clicks', label: 'Clicks', value: (r) => r.clicks, format: 'number' },
  { key: 'ctr', label: 'CTR', value: (r) => r.ctr, format: 'percent' },
  { key: 'spend', label: 'Spend', value: (r) => r.spend, format: 'currency' },
  { key: 'cpm', label: 'CPM', value: (r) => r.cpm, format: 'currency' },
  { key: 'reach', label: 'Reach', value: (r) => r.reach || null, format: 'number' },
  { key: 'frequency', label: 'Frequency', value: (r) => r.frequency ?? null, format: 'decimal' },
  { key: 'video_views', label: 'Video Views', value: (r) => r.video_views ?? 0, format: 'number' },
  { key: 'completes', label: 'Completes', value: (r) => r.completes ?? 0, format: 'number' },
];

export function SpotifyAdsSection({ client, selection, block, loading, onRetry, refreshing, showTables = true }: Props) {
  const fmt: FormatOptions = { currency: block?.currency ?? client.currency, locale: client.locale };
  const compareLabel = selection.compareRange ? formatRange(selection.compareRange) : undefined;
  const kpis = visibleMetrics(client, ALL_KPIS);
  let body = block?.status === 'not_configured'
    ? <div className="card state muted">{block.accountId ? 'La integración de Spotify Ads no está configurada en el servidor.' : 'Spotify Ads no configurado para este cliente. Puedes asignar una fuente desde Settings → Clientes.'}</div>
    : block?.status === 'auth_error'
      ? <div className="card state state-warn" role="alert">No se pudo autenticar Spotify Ads. Google Ads y Meta Ads siguen disponibles.</div>
      : block?.status === 'provider_error'
        ? <div className="card state state-warn" role="alert">Spotify Ads no está disponible temporalmente. Google Ads y Meta Ads siguen disponibles.</div>
    : blockStatusView(
    { block, loading, onRetry },
    {
      clientName: client.name,
      isEmpty: (period) => period.campaigns.length === 0,
      skeleton: <><KpiSkeleton count={kpis.length} />{showTables && <TableSkeleton />}</>,
    },
  );

  if (!body && block?.current) {
    const current = block.current;
    const subtitle = formatRange(selection.range, true);
    const campaignColumns = filterHiddenColumns(client, performanceColumns('campaign'));
    const adSetColumns = filterHiddenColumns(client, performanceColumns('adset'));
    const adColumns = filterHiddenColumns(client, performanceColumns('ad'));
    body = (
      <div className={refreshing ? 'refreshing' : undefined}>
        <BlockWarnings warnings={block.warnings ?? []} noun="Cuenta" />
        <KpiRow metrics={kpis} current={current.totals} previous={block.previous?.totals ?? null} fmt={fmt} compareLabel={compareLabel} />
        {showTables && (
          <>
            <LineChart
              title="Spotify Ads Spend diario"
              subtitle={subtitle}
              dates={current.daily.map((d) => d.date)}
              series={[{ key: 'spotify', label: 'Spotify Ads Spend', color: 'var(--series-spotify)', values: current.daily.map((d) => d.spend) }]}
              format={(value) => formatValue(value, 'currency', fmt)}
            />
            <DataTable title="Campaign Performance" subtitle={subtitle} rows={current.campaigns} columns={campaignColumns} rowKey={(r) => r.campaign_id} fmt={fmt} initialSort={{ key: 'spend', dir: 'desc' }} />
            <DataTable title="Ad Set Performance" subtitle={subtitle} rows={current.adSets} columns={adSetColumns} rowKey={(r) => r.ad_set_id ?? r.campaign_id} fmt={fmt} initialSort={{ key: 'spend', dir: 'desc' }} emptyMessage="No hay ad sets en el periodo." />
            <DataTable title="Ad Performance" subtitle={subtitle} rows={current.ads ?? []} columns={adColumns} rowKey={(r) => r.ad_id ?? r.ad_set_id ?? r.campaign_id} fmt={fmt} initialSort={{ key: 'spend', dir: 'desc' }} emptyMessage="No hay anuncios en el periodo." />
          </>
        )}
      </div>
    );
  }

  return (
    <section className="report-section">
      <SectionTitle title="Spotify Ads" tag="tag-spotify">
        {block?.accountId && <span className="muted small">Cuenta {block.accountId}</span>}
      </SectionTitle>
      {body}
    </section>
  );
}
