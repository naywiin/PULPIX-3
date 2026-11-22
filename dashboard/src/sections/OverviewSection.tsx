import { LineChart } from '../components/LineChart';
import { KpiSkeleton } from '../components/States';
import type { ClientConfig } from '../config/clients';
import { METRICS } from '../config/metrics';
import { eachDay, formatRange } from '../lib/dates';
import { alignDaily, combinedTotals } from '../lib/dashboard';
import { formatValue, type FormatOptions } from '../lib/format';
import { visibleMetrics } from '../config/metricVisibility';
import type { DashboardData } from '../types/dashboard';
import type { DateSelection } from '../types/report';
import { PerformancePacingSection } from './PerformancePacingSection';
import { KpiRow, SectionTitle } from './common';
import { PerformanceDetailSection } from './PerformanceDetailSection';
import type { ApiClient } from '../types/dashboard';
import { metaResultMode } from '../config/valueMetrics';
import { SalesProjectionSection } from './SalesProjectionSection';
import { ConversionsByPlatformSection } from './ConversionsByPlatformSection';

const ALL_KPIS = [METRICS.total_spend, METRICS.total_conversions, METRICS.total_cpa, METRICS.total_cpc, METRICS.total_value, METRICS.blended_roas];
const LABEL = { google_ads: 'Google Ads', meta_ads: 'Meta Ads', spotify_ads: 'Spotify Ads' } as const;

interface Props {
  client: ClientConfig;
  selection: DateSelection;
  data: DashboardData | null;
  loading: boolean;
  refreshing: boolean;
  onRetry: () => void;
  reportingRefreshNonce: number;
  reportingClient?: ApiClient | null;
  isRealBackend?: boolean;
}

export function OverviewSection({ client, selection, data, loading, refreshing, reportingRefreshNonce, reportingClient }: Props) {
  const compareLabel = selection.compareRange ? formatRange(selection.compareRange) : undefined;
  const combined = data ? combinedTotals(data) : null;
  const fmt: FormatOptions = { currency: combined?.currency ?? client.currency, locale: client.locale };
  const kpis = visibleMetrics(client, ALL_KPIS);

  let combinedBody: React.ReactNode;
  if (loading || !data || !combined) combinedBody = <KpiSkeleton count={kpis.length} />;
  else if (combined.included.length === 0)
    combinedBody = <p className="card note">No hay datos de plataformas paid media disponibles para combinar.</p>;
  else if (!combined.current) combinedBody = <p className="card note">No hay datos de plataformas disponibles para este periodo.</p>;
  else {
    const missing = (['google_ads', 'meta_ads', 'spotify_ads'] as const).filter((p) => !combined.included.includes(p));
    combinedBody = (
      <div className={refreshing ? 'refreshing' : undefined}>
        {missing.length > 0 && (
          <p className="note-inline small">
            Solo incluye {combined.included.map((p) => LABEL[p]).join(' y ')} · {missing.map((p) => LABEL[p]).join(', ')} no disponible.
          </p>
        )}
        {combined.currency == null && <p className="note-inline small">Hay monedas distintas. No se combinan importes ni ratios; el conteo de eventos sigue siendo válido.</p>}
        <KpiRow metrics={combined.currency == null ? kpis.filter((metric) => metric.key === 'conversions') : kpis.map((metric) => ({
          ...metric,
          label: metric.key === 'conversions' && combined.current!.conversions_partial ? 'Conversiones totales · parcial'
            : metric.key === 'cpa' && combined.current!.cpa_partial ? 'CPA total · parcial'
              : metric.key === 'conversion_value' && combined.current!.value_partial ? 'Valor de conversión · parcial'
                : metric.key === 'roas' && combined.current!.roas_partial ? 'Blended ROAS · parcial' : metric.label,
        }))} current={combined.current} previous={combined.previous} fmt={fmt} compareLabel={compareLabel} />
        <p className="muted small">Eventos atribuidos: Google Ads = Conversiones{combined.included.includes('meta_ads') ? ` · Meta Ads = ${metaResultMode(client.id) === 'purchase' ? 'Compras' : 'Clientes potenciales'}` : ''}. Son eventos por plataforma, no personas únicas deduplicadas.</p>
        {combined.current.conversions_partial && <p className="note-inline small">Conversiones parciales: {combined.current.conversion_sources.map((p) => LABEL[p]).join(' y ') || 'ninguna plataforma'} entregaron su evento configurado; {combined.included.filter((p) => (p === 'google_ads' || p === 'meta_ads') && !combined.current!.conversion_sources.includes(p)).map((p) => LABEL[p]).join(', ') || 'otra fuente'} no está disponible para este total.</p>}
        {combined.current.cpa_partial && <p className="note-inline small">CPA parcial: incluye únicamente {combined.current.cpa_sources.map((p) => LABEL[p]).join(' y ') || 'plataformas con gasto y evento válidos'}. El gasto de las plataformas sin evento medido no entra al numerador.</p>}
        {combined.current.value_partial && <p className="note-inline small">Valor de conversión y ROAS parciales: {combined.current.value_sources.map((p) => LABEL[p]).join(' y ') || 'ninguna plataforma'} tienen un valor compatible con el objetivo de este cliente.</p>}
        <p className="muted small">CPA se recalcula con gasto y eventos de las mismas plataformas. CPC usa gasto y clics de las plataformas incluidas; los importes requieren una moneda común.</p>
      </div>
    );
  }

  const dates = eachDay(selection.range).filter((d) => !data || d <= data.range.end);
  const gads = data?.google_ads.status === 'ok' ? data.google_ads.current?.daily : undefined;
  const meta = data?.meta_ads.status === 'ok' ? data.meta_ads.current?.daily : undefined;
  const spotify = data?.spotify_ads.status === 'ok' ? data.spotify_ads.current?.daily : undefined;
  const pinterest = data?.pinterest_ads?.status === 'ok' ? data.pinterest_ads.current?.pinterestDaily : undefined;
  const pinterestByDay = new Map((pinterest ?? []).map((point) => [point.date, point.spend]));
  const activeCurrencies = data
    ? [gads && data.google_ads.currency, meta && data.meta_ads.currency, spotify && data.spotify_ads.currency, pinterest && data.pinterest_ads?.currency].filter(Boolean)
    : [];
  const sameCurrency = new Set(activeCurrencies).size <= 1;
  const series = [
    gads && { key: 'gads', label: 'Gasto Google Ads', color: 'var(--series-gads)', values: alignDaily(dates, gads, 'spend') },
    meta && { key: 'meta', label: 'Gasto Meta Ads', color: 'var(--series-meta)', values: alignDaily(dates, meta, 'spend') },
    spotify && { key: 'spotify', label: 'Gasto Spotify Ads', color: 'var(--series-spotify)', values: alignDaily(dates, spotify, 'spend') },
    pinterest && { key: 'pinterest', label: 'Gasto Pinterest Ads', color: '#bd081c', values: dates.map((day) => pinterestByDay.has(day) ? pinterestByDay.get(day) ?? null : null) },
  ].filter((s): s is NonNullable<typeof s> => Boolean(s));

  return (
    <>
      <section className="report-section">
        <SectionTitle title="Total Paid Media" tag="tag-total" />
        {combinedBody}
        {data && series.length > 0 && sameCurrency && (
          <div className={refreshing ? 'refreshing' : undefined}>
            <LineChart
              title="Gasto diario por plataforma"
              subtitle={formatRange(selection.range, true)}
              dates={dates}
              series={series}
              legend
              format={(v) => formatValue(v, 'currency', { ...fmt, currency: gads ? data.google_ads.currency : meta ? data.meta_ads.currency : spotify ? data.spotify_ads.currency : data.pinterest_ads?.currency ?? fmt.currency })}
            />
          </div>
        )}
      </section>
      {data?.source === 'real' && <ConversionsByPlatformSection
        clientId={client.id}
        clientName={client.name}
        currency={client.currency}
        locale={client.locale}
        range={selection.range}
        data={data}
        reportingClient={reportingClient ?? null}
        refreshNonce={reportingRefreshNonce}
      />}
      {data?.source === 'real' && <PerformancePacingSection
        clientId={client.id}
        currency={client.currency}
        locale={client.locale}
        range={selection.range}
        refreshNonce={reportingRefreshNonce}
      />}
      {data?.source === 'real' && <SalesProjectionSection
        clientId={client.id}
        currency={client.currency}
        locale={client.locale}
        range={selection.range}
        data={data}
      />}
      {data?.source === 'real' && <PerformanceDetailSection
        clientId={client.id}
        currency={client.currency}
        locale={client.locale}
        range={selection.range}
        refreshNonce={reportingRefreshNonce}
        availablePlatforms={[
          ...(reportingClient?.google_ads_sources?.length ? ['google_ads' as const] : []),
          ...(reportingClient?.meta_ads_sources?.length ? ['meta_ads' as const] : []),
          ...(reportingClient?.spotify_ads_sources?.length ? ['spotify_ads' as const] : []),
          ...(reportingClient?.pinterest_ads_sources?.length ? ['pinterest_ads' as const] : []),
        ]}
        reportingClient={reportingClient ?? null}
      />}
    </>
  );
}
