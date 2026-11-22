import { useMemo } from 'react';
import { LineChart } from '../components/LineChart';
import { metaResultMode, valueMetricsEnabled } from '../config/valueMetrics';
import { formatValue } from '../lib/format';
import { buildSalesProjections, type ProjectionSource } from '../lib/salesProjection';
import type { DashboardData } from '../types/dashboard';
import type { DateRange } from '../types/report';
import { SectionTitle } from './common';

interface Props { clientId: string; currency: string; locale: string; range: DateRange; data: DashboardData }

export function SalesProjectionSection({ clientId, currency, locale, range, data }: Props) {
  const projections = useMemo(() => {
    if (!valueMetricsEnabled(clientId)) return [];
    const sources: ProjectionSource[] = [
      { key: 'google_ads', label: 'Google Ads', currency: data.google_ads.currency || null, available: data.google_ads.status === 'ok' && data.google_ads.current != null, daily: data.google_ads.current?.daily ?? [] },
      // The current Meta conversion_value is tied to the selected Purchase action.
      // Lead-mode clients cannot use purchase value as a proxy for lead value.
      { key: 'meta_ads', label: 'Facebook / Meta Ads', currency: data.meta_ads.currency || null, available: metaResultMode(clientId) === 'purchase' && data.meta_ads.status === 'ok' && data.meta_ads.current != null, daily: data.meta_ads.current?.daily ?? [] },
    ];
    return buildSalesProjections(range, sources, new Date().toISOString().slice(0, 10));
  }, [clientId, data, range.start, range.end]);

  if (!valueMetricsEnabled(clientId)) return null;
  const money = (value: number | null, curr: string | null) => value == null || !curr
    ? '—' : formatValue(value, 'currency', { currency: curr, locale });

  return <section className="report-section sales-projection-section" aria-label="Proyección de valor de ventas">
    <SectionTitle title="Proyección de valor de ventas" tag="tag-total" />
    <p className="muted small">Estimación al ritmo del periodo seleccionado basada en el valor de conversión atribuido por las plataformas publicitarias. No representa ingresos comprobados de WooSync.</p>
    <p className="muted small">Fórmula: valor de conversión observado ÷ días observados × días del mes. El gráfico muestra valor acumulado observado y una línea de proyección al cierre.</p>
    {projections.map((month) => {
      const projectedLabel = month.complete ? 'Valor real final del mes' : month.closed ? 'Cierre real no disponible' : 'Proyección al cierre del mes';
      const googleObserved = month.observedBySource.google_ads ?? null;
      const metaObserved = month.observedBySource.meta_ads ?? null;
      const googleProjected = month.projectedBySource.google_ads ?? null;
      const metaProjected = month.projectedBySource.meta_ads ?? null;
      const includedPlatforms = [googleObserved != null ? 'Google Ads' : null, metaObserved != null ? 'Meta Ads' : null].filter(Boolean);
      const values = [
        { key: 'google', title: 'Google Ads', observed: googleObserved, projected: googleProjected, curr: data.google_ads.currency || null, available: googleObserved != null },
        { key: 'meta', title: 'Facebook / Meta Ads', observed: metaObserved, projected: metaProjected, curr: data.meta_ads.currency || null, available: metaObserved != null },
        { key: 'total', title: month.totalPartial ? 'Total parcial' : 'Total comparable', observed: month.observedTotal, projected: month.projectedTotal, curr: month.currency, available: month.observedTotal != null },
      ];
      return <article className="sales-projection-month" key={month.key}>
        <h3>{month.label}</h3>
        <div className="sales-projection-cards">
          {values.map((item) => <div className="card sales-projection-kpi" key={item.key}>
            <span className="muted small">{item.title} · valor observado</span>
            <strong>{item.available ? money(item.observed, item.curr) : 'No disponible'}</strong>
            <span className="muted small">{projectedLabel}</span>
            <strong>{item.available && item.projected != null ? money(item.projected, item.curr) : 'No disponible'}</strong>
          </div>)}
        </div>
        {month.mixedCurrency && <p className="note-inline small">Las plataformas usan monedas distintas; se muestran por separado y no se calcula un total monetario combinado.</p>}
        {month.totalPartial && <p className="note-inline small">Total parcial: incluye {includedPlatforms.join(' y ') || 'ninguna plataforma'} con valor disponible en {month.currency}.</p>}
        {metaResultMode(clientId) === 'lead' && <p className="muted small">Meta no aporta valor monetario compatible con el resultado configurado.</p>}
        {month.closed && !month.complete && <p className="note-inline small">No se extrapolan meses cerrados: el periodo consultado no aporta valor diario suficiente para reconstruir el cierre mensual.</p>}
        {!month.series.length
          ? <div className="card state muted">No hay valor de conversión disponible para proyectar en {month.label}.</div>
          : month.mixedCurrency
            ? (['google_ads', 'meta_ads'] as const).map((platform) => {
              const series = month.series.filter((item) => item.key.startsWith(`${platform}-`));
              if (!series.length) return null;
              const label = platform === 'google_ads' ? 'Google Ads' : 'Meta Ads';
              const curr = platform === 'google_ads' ? data.google_ads.currency : data.meta_ads.currency;
              return <LineChart key={platform} title={`${label} · observado y proyección · ${month.label}`} subtitle={`Acumulado · ${month.daysObserved} días con valor observado de ${month.daysInMonth}`} dates={month.dates} series={series} legend format={(value) => formatValue(value, 'currency', { currency: curr || currency, locale })} />;
            })
            : <LineChart title={`Valor observado y cierre proyectado · ${month.label}`} subtitle={`Acumulado · ${month.daysObserved} días con valor observado de ${month.daysInMonth}`} dates={month.dates} series={month.series} legend format={(value) => formatValue(value, 'currency', { currency: month.currency ?? currency, locale })} />}
      </article>;
    })}
    {!projections.length && <div className="card state muted">No hay meses dentro del periodo seleccionado.</div>}
  </section>;
}
