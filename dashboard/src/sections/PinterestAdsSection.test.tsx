import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { ClientConfig } from '../config/clients';
import type { AdsPeriod, PlatformBlock, PinterestMetrics } from '../types/dashboard';
import type { DateSelection } from '../types/report';
import { PinterestAdsSection } from './PinterestAdsSection';

const client: ClientConfig = {
  id: 'ssiento', name: 'Ssiento', reportName: 'Ssiento', brandColor: '#000', currency: 'MXN',
  locale: 'es-MX', timezone: 'America/Mexico_City', sources: {},
};
const selection: DateSelection = {
  preset: 'custom', range: { start: '2026-10-01', end: '2026-10-14' }, compare: 'none', compareRange: null,
};
const metrics: PinterestMetrics = {
  spend: 100, impressions: 2000, clicks: 100, ctr: .05, cpc: 1, checkouts: 5,
  cost_per_checkout: 20, conversion_value: 500, roas: 5, currency: 'MXN',
};
function block(status: PlatformBlock<AdsPeriod>['status']): PlatformBlock<AdsPeriod> {
  return {
    platform: 'pinterest_ads', label: 'Pinterest Ads', source: 'real', status, accountId: '549763101458', currency: 'MXN',
    current: {
      totals: {} as AdsPeriod['totals'], campaigns: [], adSets: [], daily: [],
      pinterestTotals: metrics, pinterestDaily: [],
    },
    previous: null,
    warnings: status === 'partial' ? [{ accountId: '549763101458', status: 'daily_series_partial', message: 'Serie diaria temporalmente no disponible.' }] : [],
  };
}
function render(status: PlatformBlock<AdsPeriod>['status']) {
  return renderToStaticMarkup(<PinterestAdsSection client={client} selection={selection} block={block(status)} loading={false} onRetry={() => {}} />);
}

describe('Pinterest Ads page status handling', () => {
  it('renders normalized real metric cards for status ok', () => {
    const html = render('ok');
    for (const label of ['Gasto', 'Impresiones', 'Clics', 'CTR', 'CPC', 'Checkouts', 'CPA / costo por checkout', 'Valor de conversión', 'ROAS']) {
      expect(html).toContain(label);
    }
    expect(html).not.toContain('no está disponible temporalmente');
    expect(html).toContain('$100');
    expect(html).toContain('$500');
  });

  it('keeps summary cards visible when status is partial', () => {
    const html = render('partial');
    expect(html).toContain('Datos parciales de Pinterest Ads.');
    expect(html).toContain('Serie diaria temporalmente no disponible.');
    expect(html).toContain('Checkouts');
    expect(html).toContain('$500');
  });

  it('uses a clear provider error instead of the generic temporary-unavailable text', () => {
    const html = render('provider_error');
    expect(html).toContain('No fue posible consultar Pinterest Ads.');
    expect(html).not.toContain('Pinterest Ads no está disponible temporalmente.');
  });

  it('distinguishes authentication errors', () => {
    expect(render('auth_error')).toContain('Error de autenticación de Pinterest Ads.');
  });
});
