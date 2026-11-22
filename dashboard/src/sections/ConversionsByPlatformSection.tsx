import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/backend';
import { ConversionSpendChart, type ConversionSpendDay } from '../components/ConversionSpendChart';
import { LineChart } from '../components/LineChart';
import { metaResultMode } from '../config/valueMetrics';
import { eachDay, formatRange } from '../lib/dates';
import { analyzeConversions, useAggregateConversionTotals } from '../lib/conversionAnalysis';
import { formatValue, type FormatOptions } from '../lib/format';
import { type DetailStatusFilter, type DetailSpendFilter } from '../lib/performanceDetailFilters';
import type { DateRange } from '../types/report';
import type { ApiClient, DashboardData } from '../types/dashboard';
import type { PerformanceDetailRow } from '../types/reporting';
import { SectionTitle } from './common';

type ConversionPlatform = 'google_ads' | 'meta_ads';
type PlatformChoice = 'all' | ConversionPlatform;
const LABEL = { google_ads: 'Google Ads', meta_ads: 'Meta Ads' } as const;

interface Props {
  clientId: string;
  clientName: string;
  currency: string;
  locale: string;
  range: DateRange;
  data: DashboardData;
  reportingClient: ApiClient | null;
  refreshNonce: number;
}

export function ConversionsByPlatformSection({ clientId, clientName, currency, locale, range, data, reportingClient, refreshNonce }: Props) {
  const [platform, setPlatform] = useState<PlatformChoice>('all');
  const [status, setStatus] = useState<DetailStatusFilter>('all');
  const [spend, setSpend] = useState<DetailSpendFilter>('all');
  const [rows, setRows] = useState<PerformanceDetailRow[]>([]);
  const [loadedPlatforms, setLoadedPlatforms] = useState<ConversionPlatform[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const configuredPlatforms = useMemo(() => [
    ...(reportingClient?.google_ads_sources?.length ? ['google_ads' as const] : []),
    ...(reportingClient?.meta_ads_sources?.length ? ['meta_ads' as const] : []),
  ], [reportingClient]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setErrors([]); setRows([]); setLoadedPlatforms([]);
    if (!configuredPlatforms.length) { setLoading(false); return () => controller.abort(); }
    Promise.allSettled(configuredPlatforms.map((item) => api.performanceDetail(
      clientId, item, range.start, range.end, { level: 'campaign', status: 'all', spend: 'all' }, controller.signal,
    )))
      .then((results) => {
        if (controller.signal.aborted) return;
        const nextRows: PerformanceDetailRow[] = [];
        const nextLoaded: ConversionPlatform[] = [];
        const nextErrors: string[] = [];
        results.forEach((result, index) => {
          const currentPlatform = configuredPlatforms[index];
          if (result.status === 'fulfilled') { nextRows.push(...result.value.rows); nextLoaded.push(currentPlatform); }
          else nextErrors.push(`${LABEL[currentPlatform]}: ${result.reason instanceof Error ? result.reason.message : String(result.reason)}`);
        });
        setRows(nextRows); setLoadedPlatforms(nextLoaded); setErrors(nextErrors);
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [clientId, range.start, range.end, refreshNonce, configuredPlatforms]);

  const dates = eachDay(range);
  const dataEnd = rows.reduce<string | null>((latest, row) => (row.daily ?? []).reduce<string | null>((current, point) =>
    point.date > (current ?? '') ? point.date : current, latest), null);
  const visibleDates = dates.filter((date) => !dataEnd || date <= dataEnd);
  const selectedPlatforms = platform === 'all' ? configuredPlatforms : configuredPlatforms.includes(platform) ? [platform] : [];
  const selectedRows = rows.filter((row) => selectedPlatforms.includes(row.platform as ConversionPlatform));
  const selectedLoaded = loadedPlatforms.filter((item) => selectedPlatforms.includes(item));
  const rowAnalysis = useMemo(() => analyzeConversions(selectedRows, visibleDates, {
    status, spend, metaMode: metaResultMode(clientId), availablePlatforms: selectedLoaded,
  }), [selectedRows, visibleDates, status, spend, clientId, selectedLoaded]);
  const analysis = useMemo(() => {
    if (status !== 'all' || spend !== 'all') return rowAnalysis;
    const source = (item: ConversionPlatform) => {
      const block = item === 'google_ads' ? data.google_ads : data.meta_ads;
      return block.status === 'ok' && block.current ? {
        available: true, currency: block.currency || null,
        totals: block.current.totals,
        daily: block.current.daily,
      } : undefined;
    };
    return useAggregateConversionTotals(rowAnalysis, {
      google_ads: source('google_ads'), meta_ads: source('meta_ads'),
    }, visibleDates, metaResultMode(clientId));
  }, [rowAnalysis, status, spend, data.google_ads, data.meta_ads, selectedLoaded, visibleDates, clientId]);

  const fmt: FormatOptions = { currency, locale };
  const eventLabel = metaResultMode(clientId) === 'purchase' ? 'Compras' : 'Clientes potenciales';
  const selectedSummaries = selectedPlatforms.map((item) => analysis[item]).filter((item) => item.available);
  const selectedErrors = errors.filter((error) => selectedPlatforms.some((item) => error.startsWith(`${LABEL[item]}:`)));
  const hasSourceErrors = selectedErrors.length > 0;
  const countTotal = new Set(rows.map((row) => `${row.platform}:${row.source_account_id}:${row.campaign_id}`)).size;
  const countVisible = new Set(analysis.visibleRows.map((row) => `${row.platform}:${row.source_account_id}:${row.campaign_id}`)).size;
  const partialByMetric = selectedSummaries.some((item) => item.partialConversions);
  const conversionSeries = selectedSummaries.map((item) => ({
    key: item.platform,
    label: LABEL[item.platform as ConversionPlatform],
    color: item.platform === 'google_ads' ? 'var(--series-gads)' : 'var(--series-meta)',
    values: item.daily.map((point) => point.conversions),
  }));
  const googleCurrency = analysis.google_ads.currency;
  const metaCurrency = analysis.meta_ads.currency;
  const chartDays: ConversionSpendDay[] = visibleDates.map((date, index) => {
    const google = platform === 'meta_ads' ? null : analysis.google_ads.daily[index];
    const meta = platform === 'google_ads' ? null : analysis.meta_ads.daily[index];
    const googleConversions = analysis.google_ads.available && platform !== 'meta_ads' ? google?.conversions ?? null : null;
    const metaConversions = analysis.meta_ads.available && platform !== 'google_ads' ? meta?.conversions ?? null : null;
    const conversionValues = [googleConversions, metaConversions].filter((value): value is number => value != null);
    const conversionsTotal = conversionValues.length ? conversionValues.reduce((sum, value) => sum + value, 0) : null;
    const googleSpend = platform === 'meta_ads' ? null : google?.spend ?? null;
    const metaSpend = platform === 'google_ads' ? null : meta?.spend ?? null;
    const cpaGoogle = analysis.cpaSources.includes('google_ads') && platform !== 'meta_ads';
    const cpaMeta = analysis.cpaSources.includes('meta_ads') && platform !== 'google_ads';
    const cpaSpendValues = [cpaGoogle ? googleSpend : null, cpaMeta ? metaSpend : null];
    const totalSpend = analysis.mixedCurrency || !cpaSpendValues.some((value) => value != null)
      || cpaSpendValues.some((value, index) => (index === 0 ? cpaGoogle : cpaMeta) && value == null)
      ? null : cpaSpendValues.reduce<number>((sum, value) => sum + (value ?? 0), 0);
    return { date, googleConversions, metaConversions, conversionsTotal: conversionValues.length ? conversionsTotal : null,
      googleSpend, metaSpend, totalSpend };
  });
  const shownCurrency = analysis.totalCurrency ?? selectedSummaries.find((item) => item.currency)?.currency ?? currency;

  return <section className="report-section overview-conversions" aria-label="Conversiones por plataforma">
    <SectionTitle title="Conversiones por plataforma" tag="tag-total" />
    <div className="detail-controls overview-conversion-controls">
      <label>Plataforma<select aria-label="Plataforma de conversiones" value={platform} onChange={(event) => setPlatform(event.target.value as PlatformChoice)}>
        <option value="all">Google Ads y Meta Ads</option><option value="google_ads">Solo Google Ads</option><option value="meta_ads">Solo Meta Ads</option>
      </select></label>
      <label>Estado<select aria-label="Estado de conversiones" value={status} onChange={(event) => setStatus(event.target.value as DetailStatusFilter)}>
        <option value="all">Todos</option><option value="active">Activos</option><option value="inactive">Inactivos</option><option value="unknown">Desconocidos</option>
      </select></label>
      <label>Gasto<select aria-label="Gasto de conversiones" value={spend} onChange={(event) => setSpend(event.target.value as DetailSpendFilter)}>
        <option value="all">Todos</option><option value="with_spend">Con gasto</option><option value="without_spend">Sin gasto</option>
      </select></label>
    </div>
    <p className="muted small conversion-visible-count">{loading ? 'Cargando campañas…' : `${countVisible} campañas visibles de ${countTotal}`}</p>
    {selectedErrors.map((error) => <p key={error} className="note-inline small">{error}</p>)}
    {analysis.mixedCurrency && <p className="note-inline small">Las plataformas utilizan monedas distintas. No se combinan gastos ni se calcula CPA total.</p>}
    <div className="pacing-summary-grid conversion-kpis">
      {selectedPlatforms.includes('google_ads') && <article className="card pacing-kpi"><span className="muted small">Conversiones · Google Ads</span><strong>{analysis.google_ads.conversions == null ? 'No disponible' : formatValue(analysis.google_ads.conversions, 'decimal', fmt)}</strong>{analysis.google_ads.partialConversions && <span className="muted small">Parcial: hay campañas sin desglose</span>}</article>}
      {selectedPlatforms.includes('meta_ads') && <article className="card pacing-kpi"><span className="muted small">Conversiones · Meta Ads ({eventLabel})</span><strong>{analysis.meta_ads.conversions == null ? 'No disponible' : formatValue(analysis.meta_ads.conversions, 'decimal', fmt)}</strong>{analysis.meta_ads.partialConversions && <span className="muted small">Parcial: hay campañas sin desglose</span>}</article>}
      {platform === 'all' && <article className="card pacing-kpi"><span className="muted small">Conversiones totales</span><strong>{analysis.conversionsTotal == null ? 'No disponible' : formatValue(analysis.conversionsTotal, 'decimal', fmt)}</strong>{(analysis.partialTotal || hasSourceErrors) && <span className="muted small">Total parcial de eventos atribuidos</span>}</article>}
    </div>
    <p className="muted small">Eventos atribuidos por las plataformas. No representan personas únicas deduplicadas entre Google y Meta. Google Ads: conversiones reportadas por Google. Meta Ads: {eventLabel} para {clientName}.</p>
    {loading ? <p className="card note">Cargando conversiones del periodo…</p> : conversionSeries.length === 0
      ? <p className="card note">No hay campañas disponibles para las plataformas seleccionadas.</p>
      : <LineChart title="Conversiones diarias por plataforma" subtitle={formatRange(range, true)} dates={visibleDates} series={conversionSeries} legend format={(value) => formatValue(value, 'decimal', fmt)} />}
      {selectedSummaries.length > 0 && <div className="conversion-cpa card"><div><strong>{analysis.cpaPartial ? 'CPA parcial del filtro actual' : 'CPA del filtro actual'}</strong><span className="muted small">Incluye {analysis.cpaSources.map((item) => LABEL[item as ConversionPlatform]).join(' y ') || 'solo fuentes con resultados y gasto válidos'} · gasto filtrado ÷ conversiones filtradas</span></div><strong>{analysis.cpa == null ? '—' : formatValue(analysis.cpa, 'currency', { ...fmt, currency: shownCurrency })}</strong></div>}
    <ConversionSpendChart days={chartDays} platform={platform} googleCurrency={googleCurrency} metaCurrency={metaCurrency}
      mixedCurrency={analysis.mixedCurrency} partial={analysis.partialTotal || analysis.cpaPartial} fmt={fmt} />
    {!visibleDates.length && <p className="card note">Sin datos diarios para el periodo.</p>}
    {partialByMetric && <p className="muted small">Los valores parciales excluyen campañas cuya métrica de conversión no está disponible; no se interpretan como cero.</p>}
    {analysis.cpaPartial && <p className="muted small">CPA parcial: el numerador incluye únicamente el gasto de las plataformas incluidas en el denominador. Las plataformas sin resultados disponibles no se agregan al CPA.</p>}
    {hasSourceErrors && <p className="muted small">Una o más fuentes no respondieron; los resultados disponibles se identifican como parciales.</p>}
  </section>;
}
