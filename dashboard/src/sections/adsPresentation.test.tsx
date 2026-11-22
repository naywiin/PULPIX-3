import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { ClientConfig } from '../config/clients';
import { deriveMetrics, safeRatio } from '../lib/metrics';
import type { AdsPeriod, PlatformBlock } from '../types/dashboard';
import type { MetricRecord } from '../types/metrics';
import type { DateSelection } from '../types/report';
import { MetaAdsSection } from './MetaAdsSection';
import { GoogleAdsSection } from './GoogleAdsSection';

const selection: DateSelection = { preset: 'custom', range: { start: '2026-09-01', end: '2026-09-30' }, compare: 'none', compareRange: null };
const client = (id: string): ClientConfig => ({ id, name: id, reportName: '', brandColor: '#000', currency: 'MXN', locale: 'es-MX', timezone: 'America/Mexico_City', sources: {} });
const base = { spend: 1000, leads: 10, impressions: 1000, clicks: 100, reach: 500, conversions: 5, conversion_value: 1500 };
function block(platform: 'meta_ads' | 'google_ads'): PlatformBlock<AdsPeriod> {
  const metric = { ...deriveMetrics(base), purchases: 5, cost_per_purchase: 200,
    cost_per_result: safeRatio(base.spend, base.leads) };
  const campaign = { ...metric, client_id: 'blumia', platform, account_id: '123', campaign_id: '1', campaign_name: 'Campaña A', date: '2026-09-01', date_end: '2026-09-30' } as MetricRecord;
  return { platform, label: platform, source: 'mock', status: 'ok', accountId: '123', currency: 'MXN',
    current: { totals: metric, campaigns: [campaign], adSets: [campaign], daily: [] }, previous: null };
}

const renderMeta = (id: string) => renderToStaticMarkup(<MetaAdsSection client={client(id)} selection={selection} block={block('meta_ads')} loading={false} onRetry={() => {}} />);
const renderGoogle = (id: string) => renderToStaticMarkup(<GoogleAdsSection client={client(id)} selection={selection} block={block('google_ads')} loading={false} onRetry={() => {}} />);

describe('presentación de plataformas', () => {
  it('Meta usa clientes potenciales y costo por resultado; no muestra terminología obsoleta', () => {
    const html = renderMeta('clarvi');
    for (const label of ['Gasto', 'Clientes potenciales', 'Costo por resultado', 'CPC', 'Impresiones', 'Clics', 'CTR', 'Alcance']) expect(html).toContain(label);
    for (const label of ['Purchases', 'Purchase', 'Cost per Purchase', 'Purchase Conversion Value', 'Purchase ROAS', 'Amount Spent', 'Costo por adquisición', 'Valor de conversión', 'ROAS']) expect(html).not.toContain(label);
  });
  it('Google usa conversiones y CPA, sin alcance ni costo por resultado', () => {
    const html = renderGoogle('clarvi');
    for (const label of ['Gasto', 'Conversiones', 'CPA', 'Costo por adquisición', 'CPC', 'Impresiones', 'Clics', 'CTR']) expect(html).toContain(label);
    for (const label of ['Alcance', 'Costo por resultado', 'Valor de conversión', 'ROAS', 'Amount Spent']) expect(html).not.toContain(label);
  });
  for (const id of ['cirque-du-soleil-joya', 'jungala', 'ssiento', 'blumia']) {
    it(`${id} ve valor y ROAS en Google y Meta`, () => {
      expect(renderMeta(id)).toContain('Valor de conversión');
      expect(renderMeta(id)).toContain('ROAS');
      expect(renderGoogle(id)).toContain('Valor de conversión');
      expect(renderGoogle(id)).toContain('ROAS');
    });
  }
  for (const id of ['cirque-du-soleil-joya', 'jungala', 'ssiento', 'blumia']) {
    it(`${id} usa Compras como resultado Meta y habilita valor/ROAS`, () => {
      const html = renderMeta(id);
      expect(html).toContain('Compras');
      expect(html).toContain('Costo por resultado');
      expect(html).toContain('Valor de conversión');
      expect(html).toContain('ROAS');
      expect(html).not.toContain('Clientes potenciales');
      expect(html).not.toContain('Costo por lead');
      expect(html).toMatch(/Compras<\/div><div class="kpi-value">5/);
      expect(html).toMatch(/Costo por resultado<\/div><div class="kpi-value">\$200/);
    });
  }
  it('divisor cero y datos faltantes producen null', () => {
    expect(safeRatio(1000, 10)).toBe(100);
    expect(safeRatio(1000, 0)).toBeNull();
    expect(safeRatio(null, 10)).toBeNull();
    expect(safeRatio(0, 10)).toBe(0);
  });
  it('los clientes lead conservan leads ausentes y cero medido', () => {
    const absent = block('meta_ads');
    absent.current!.totals.leads = null;
    absent.current!.totals.cost_per_result = null;
    const absentHtml = renderToStaticMarkup(<MetaAdsSection client={client('clarvi')} selection={selection} block={absent} loading={false} onRetry={() => {}} showTables={false} />);
    expect(absentHtml).toMatch(/Clientes potenciales<\/div><div class="kpi-value">—/);
    const zero = block('meta_ads');
    zero.current!.totals.leads = 0;
    zero.current!.totals.cost_per_result = null;
    const zeroHtml = renderToStaticMarkup(<MetaAdsSection client={client('clarvi')} selection={selection} block={zero} loading={false} onRetry={() => {}} showTables={false} />);
    expect(zeroHtml).toMatch(/Clientes potenciales<\/div><div class="kpi-value">0/);
    expect(zeroHtml).toMatch(/Costo por resultado<\/div><div class="kpi-value">—/);
  });
  it('Meta distingue compras ausentes de un cero real en la card', () => {
    const absent = block('meta_ads');
    absent.current!.totals.purchases = null;
    absent.current!.totals.cost_per_purchase = null;
    const absentHtml = renderToStaticMarkup(<MetaAdsSection client={client('jungala')} selection={selection} block={absent} loading={false} onRetry={() => {}} showTables={false} />);
    expect(absentHtml).toMatch(/Compras<\/div><div class="kpi-value">—/);
    const zero = block('meta_ads');
    zero.current!.totals.purchases = 0;
    zero.current!.totals.cost_per_purchase = null;
    const zeroHtml = renderToStaticMarkup(<MetaAdsSection client={client('jungala')} selection={selection} block={zero} loading={false} onRetry={() => {}} showTables={false} />);
    expect(zeroHtml).toMatch(/Compras<\/div><div class="kpi-value">0/);
    expect(zeroHtml).toMatch(/Costo por resultado<\/div><div class="kpi-value">—/);
  });
});
