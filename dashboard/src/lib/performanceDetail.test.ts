import { describe, expect, it } from 'vitest';
import { aggregateVisibleDetailRows, searchDetailRows } from './performanceDetail';
import type { PerformanceDetailRow } from '../types/reporting';

const item = (overrides: Partial<PerformanceDetailRow> = {}): PerformanceDetailRow => ({
  level: 'campaign',
  platform: 'google_ads',
  source_account_id: 'account-1',
  campaign_id: '1',
  campaign_name: 'Campaign One',
  status: 'PAUSED',
  status_label: 'Pausado',
  spend: 500,
  impressions: 1000,
  clicks: 100,
  conversions: 10,
  leads: null,
  conversion_value: 1200,
  cpc: 5,
  ctr: 0.1,
  cpa: 50,
  cpl: null,
  roas: 2.4,
  ...overrides,
});

describe('detalle de performance', () => {
  it('busca por nombre, campaign ID y ad ID después de filtrar en el backend', () => {
    const rows = [
      item(),
      item({ level: 'ad', campaign_id: '2', campaign_name: 'Campaign Two', ad_id: 'ad-900', ad_name: 'Summer Creative' }),
    ];
    expect(searchDetailRows(rows, 'summer')).toEqual([rows[1]]);
    expect(searchDetailRows(rows, 'ad-900')).toEqual([rows[1]]);
    expect(searchDetailRows(rows, '1')).toEqual([rows[0]]);
    expect(searchDetailRows(rows, '   ')).toEqual(rows);
  });

  it('calcula los ratios desde las bases visibles, sin promediar ratios', () => {
    const totals = aggregateVisibleDetailRows([
      item(),
      item({ campaign_id: '2', spend: 200, impressions: 100, clicks: 10, conversions: 1, leads: 2, conversion_value: 100 }),
    ]);
    expect(totals.spend).toBe(700);
    expect(totals.cpc).toBe(700 / 110);
    expect(totals.ctr).toBe(110 / 1100);
    expect(totals.cpa).toBe(700 / 11);
    expect(totals.cpl).toBe(700 / 2);
    expect(totals.roas).toBe(1300 / 700);
  });

  for (const level of ['campaign', 'ad'] as const) {
    it(`suma purchases y recalcula costo por purchase en ${level} tras la búsqueda`, () => {
      const rows = [
        item({ level, platform: 'meta_ads', campaign_name: 'Compra A', spend: 600, purchases: 3, leads: null }),
        item({ level, platform: 'meta_ads', campaign_id: '2', campaign_name: 'Compra B', spend: 400, purchases: 7, leads: null }),
      ];
      expect(aggregateVisibleDetailRows(rows).cost_per_purchase).toBe(100);
      expect(aggregateVisibleDetailRows(searchDetailRows(rows, 'Compra A')).cost_per_purchase).toBe(200);
      expect(aggregateVisibleDetailRows([item({ level, platform: 'meta_ads', purchases: 0 })]).cost_per_purchase).toBeNull();
      expect(aggregateVisibleDetailRows([item({ level, platform: 'meta_ads', purchases: null })]).purchases).toBeNull();
    });
  }

  it('no convierte métricas null ni divisores cero en cero, NaN o Infinity', () => {
    const totals = aggregateVisibleDetailRows([
      item({ spend: 0, impressions: 0, clicks: 0, conversions: 0, leads: null, conversion_value: null }),
    ]);
    expect(totals.cpc).toBeNull();
    expect(totals.ctr).toBeNull();
    expect(totals.cpa).toBeNull();
    expect(totals.cpl).toBeNull();
    expect(totals.roas).toBeNull();
    expect(totals.leads).toBeNull();
    expect(totals.conversion_value).toBeNull();
  });

  it('no suma gastos de monedas distintas en el total visible', () => {
    const totals = aggregateVisibleDetailRows([
      item({ currency: 'MXN', spend: 500 }),
      item({ currency: 'USD', campaign_id: '2', spend: 20 }),
    ]);
    expect(totals.spend).toBeNull();
    expect(totals.cpc).toBeNull();
    expect(totals.roas).toBeNull();
    expect(totals.clicks).toBe(200);
  });
});
