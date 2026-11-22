import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ReportingActions } from '../components/ReportingActions';
import { BudgetManager } from '../components/BudgetManager';
import { PerformancePacingSection } from '../sections/PerformancePacingSection';
import { OverviewSection } from '../sections/OverviewSection';
import { SalesProjectionSection } from '../sections/SalesProjectionSection';
import { PerformanceDetailSection } from '../sections/PerformanceDetailSection';
import { getClient } from '../config/clients';
import type { DashboardData } from '../types/dashboard';
import type { ApiClient } from '../types/dashboard';
import { assignedBudgetPlatforms, defaultReportFilename, validateReportPeriod } from './reporting';

const client: ApiClient = {
  id: 'santuario-residencial', name: 'Santuario', slug: 'santuario-residencial', logo: null,
  active: true, google_ads_customer_id: '111', google_ads_login_customer_id: null,
  meta_ad_account_id: '222',
  google_ads_sources: [{ customer_id: '111', login_customer_id: null, campaign_ids: [], allocation: 'exclusive' }],
  meta_ads_sources: [{ ad_account_id: '222', campaign_ids: [], allocation: 'exclusive', adset_selections: [] }],
  spotify_ads_sources: [{ business_id: 'business', ad_account_id: 'spotify' }],
  ga4_property_id: null, ga4_property_ids: [], ga4_account_id: null,
  currency: 'MXN', timezone: 'America/Mexico_City', brand_color: null, notes: null, hidden_metrics: [],
};

describe('reporting controls', () => {
  it('derives budget platforms only from current client assignments', () => {
    expect(assignedBudgetPlatforms(client)).toEqual(['google_ads', 'meta_ads', 'spotify_ads']);
    expect(assignedBudgetPlatforms({ ...client, google_ads_sources: [], meta_ads_sources: [], spotify_ads_sources: [] })).toEqual([]);
  });

  it('validates only the requested report period', () => {
    expect(validateReportPeriod('2026-09-01', '2026-09-30')).toEqual({ valid: true });
    expect(validateReportPeriod('2026-09-30', '2026-09-01').error).toContain('Desde');
    expect(validateReportPeriod('', '2026-09-01').valid).toBe(false);
  });

  it('uses the required filename', () => {
    expect(defaultReportFilename('2026-08-01', '2026-09-30')).toBe('Reporte_Performance_2026-08-01_al_2026-09-30.xlsx');
  });

  it('renders both dashboard actions', () => {
    const html = renderToStaticMarkup(<ReportingActions range={{ start: '2026-09-01', end: '2026-09-30' }} />);
    expect(html).toContain('Administrar presupuestos');
    expect(html).toContain('Exportar Excel');
  });

  it('renders the groups and budgets editor inline, without a modal overlay', () => {
    const html = renderToStaticMarkup(<BudgetManager client={client} range={{ start: '2026-09-01', end: '2026-10-15' }} />);
    expect(html).toContain('Grupos y presupuestos');
    expect(html).toContain('reporting-groups-budget');
    expect(html).not.toContain('aria-modal="true"');
    expect(html).toContain('Nuevo grupo');
    expect(html).toContain('Presupuestos de');
    expect(html).toContain('Octubre 2026');
    expect(html).toContain('Septiembre 2026');
    expect(html).toContain('Por grupo');
    expect(html).toContain('Por campaña');
    expect(html).toContain('No hay grupos configurados para este mes');
    expect(html).toContain('No hay presupuestos capturados');
  });

  it('monta BudgetManager desde Overview junto a Detalle, incluso sin grupos ni presupuestos', () => {
    const emptyBlock = (platform: 'google_ads' | 'meta_ads' | 'spotify_ads' | 'ga4') => ({
      platform, label: platform, status: 'not_linked' as const, source: 'real' as const,
      accountId: null, currency: 'MXN', current: null, previous: null,
    });
    const data: DashboardData = {
      source: 'real', clientId: client.id, range: { start: '2026-10-01', end: '2026-10-12' },
      compareRange: null, generatedAt: '2026-10-12T00:00:00Z',
      google_ads: emptyBlock('google_ads'), meta_ads: emptyBlock('meta_ads'),
      spotify_ads: emptyBlock('spotify_ads'), ga4: emptyBlock('ga4'),
    };
    const html = renderToStaticMarkup(<OverviewSection
      client={getClient('blumia')} selection={{ preset: 'this_month', range: data.range, compare: 'none', compareRange: null }}
      data={{ ...data, clientId: 'blumia' }} loading={false} refreshing={false} onRetry={() => undefined}
      reportingRefreshNonce={0} reportingClient={{ ...client, id: 'blumia', name: 'Blumia' }} isRealBackend
    />);
    expect(html).toContain('Detalle de Performance');
    expect(html).toContain('Conversiones por plataforma');
    expect(html).not.toContain('Leads adquiridos por plataforma');
    expect(html).toContain('Grupos y presupuestos');
    expect(html).toContain('Por grupo');
    expect(html).toContain('Por campaña');
    expect(html).toContain('No hay grupos configurados para este mes');
    expect(html).toContain('Proyección de valor de ventas');
  });

  it('muestra filtros independientes y la gráfica Conversiones vs Gasto desde Overview', () => {
    const emptyBlock = (platform: 'google_ads' | 'meta_ads' | 'spotify_ads' | 'ga4') => ({
      platform, label: platform, status: 'not_linked' as const, source: 'real' as const,
      accountId: null, currency: 'MXN', current: null, previous: null,
    });
    const data: DashboardData = {
      source: 'real', clientId: client.id, range: { start: '2026-10-01', end: '2026-10-12' },
      compareRange: null, generatedAt: '2026-10-12T00:00:00Z',
      google_ads: emptyBlock('google_ads'), meta_ads: emptyBlock('meta_ads'),
      spotify_ads: emptyBlock('spotify_ads'), ga4: emptyBlock('ga4'),
    };
    const html = renderToStaticMarkup(<OverviewSection
      client={getClient('cirque-du-soleil-joya')} selection={{ preset: 'this_month', range: data.range, compare: 'none', compareRange: null }}
      data={{ ...data, clientId: 'cirque-du-soleil-joya' }} loading={false} refreshing={false} onRetry={() => undefined}
      reportingRefreshNonce={0} reportingClient={{ ...client, id: 'cirque-du-soleil-joya', name: 'Cirque du Soleil JOYÀ' }} isRealBackend
    />);
    expect(html).toContain('Conversiones por plataforma');
    expect(html).toContain('aria-label="Estado de conversiones"');
    expect(html).toContain('aria-label="Gasto de conversiones"');
    expect(html).toContain('CONVERSIONES VS GASTO');
  });

  it('expone filtros independientes de plataforma, estado desconocido y gasto', () => {
    const html = renderToStaticMarkup(<PerformanceDetailSection
      clientId="blumia" currency="MXN" locale="es-MX" range={{ start: '2026-10-01', end: '2026-10-12' }} refreshNonce={0}
      availablePlatforms={['google_ads', 'meta_ads']} reportingClient={client}
    />);
    expect(html).toContain('aria-label="Filtrar por estado"');
    expect(html).toContain('Desconocidos');
    expect(html).toContain('aria-label="Filtrar por gasto"');
    expect(html).toContain('Con gasto');
    expect(html).toContain('Sin gasto');
    expect(html).toContain('Nombre o ID de campaña');
    expect(html).toContain('Plataforma');
    expect(html).toContain('Grupo');
  });

  it('monta la gráfica de valor observado y proyección por separado para cada mes', () => {
    const block = (platform: 'google_ads' | 'meta_ads', value: number) => ({
      platform, label: platform, status: 'ok' as const, source: 'real' as const, accountId: 'synthetic', currency: 'MXN',
      current: { daily: [{ date: '2026-10-01', spend: 1, conversions: null, conversion_value: value, clicks: 0, impressions: 0 }] },
    });
    const data = { google_ads: block('google_ads', 100), meta_ads: block('meta_ads', 50) } as unknown as DashboardData;
    const html = renderToStaticMarkup(<SalesProjectionSection clientId="cirque-du-soleil-joya" currency="MXN" locale="es-MX" range={{ start: '2026-10-01', end: '2026-10-01' }} data={data} />);
    expect(html).toContain('Proyección de valor de ventas');
    expect(html).toContain('Valor observado y cierre proyectado');
    expect(html).toContain('Google Ads · observado');
    expect(html).toContain('Facebook / Meta Ads · proyección');
    expect(html).toContain('Total comparable · valor observado');
    expect(html).toContain('No representa ingresos comprobados de WooSync');
  });

  it('Ssiento incluye el Purchase value de Meta en la proyección', () => {
    const block = (platform: 'google_ads' | 'meta_ads', value: number) => ({
      platform, label: platform, status: 'ok' as const, source: 'real' as const, accountId: 'synthetic', currency: 'MXN',
      current: { daily: [{ date: '2026-10-01', spend: 1, conversions: null, conversion_value: value, clicks: 0, impressions: 0 }] },
    });
    const data = { google_ads: block('google_ads', 100), meta_ads: block('meta_ads', 50) } as unknown as DashboardData;
    const html = renderToStaticMarkup(<SalesProjectionSection clientId="ssiento" currency="MXN" locale="es-MX" range={{ start: '2026-10-01', end: '2026-10-01' }} data={data} />);
    expect(html).toContain('Google Ads · valor observado');
    expect(html).toContain('Facebook / Meta Ads · valor observado');
    expect(html).toContain('Total comparable · valor observado');
    expect(html).toContain('Meta Ads · observado</li>');
  });

  it('renders Performance y Pacing using the existing global date range', () => {
    const html = renderToStaticMarkup(<PerformancePacingSection
      clientId={client.id}
      currency={client.currency}
      locale="es-MX"
      range={{ start: '2026-09-01', end: '2026-09-15' }}
      refreshNonce={0}
    />);
    expect(html).toContain('Performance y Pacing');
    expect(html).toContain('2026-09-01 → 2026-09-15');
    expect(html).not.toContain('type="date"');
  });
});
