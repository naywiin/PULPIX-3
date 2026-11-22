import { describe, expect, it } from 'vitest';
import type { PerformanceDetailRow } from '../types/reporting';
import { analyzeConversions, useAggregateConversionTotals } from './conversionAnalysis';

const make = (platform: 'google_ads' | 'meta_ads', id: string, status: string | null, spend: number | null,
  counts: { conversions?: number | null; purchases?: number | null; leads?: number | null }, currency = 'MXN'): PerformanceDetailRow => ({
  level: 'campaign', platform, source_account_id: 'test-acct', campaign_id: id, campaign_name: id,
  status, effective_status: platform === 'meta_ads' ? status : null, status_label: status ?? 'Desconocido',
  spend, impressions: 100, clicks: 10, conversions: counts.conversions ?? null, purchases: counts.purchases ?? null,
  leads: counts.leads ?? null, conversion_value: null, cpc: null, ctr: null, cpa: null, cpl: null, roas: null,
  currency,
  daily: [{ date: '2026-10-01', spend, conversions: counts.conversions ?? null, purchases: counts.purchases ?? null, leads: counts.leads ?? null }],
});

const dates = ['2026-10-01'];
const available = ['google_ads', 'meta_ads'] as const;

describe('conversions by platform after independent detail filters', () => {
  const gRows = [
    make('google_ads', 'g-active-spend', 'ENABLED', 100, { conversions: 10 }),
    make('google_ads', 'g-active-zero', 'ENABLED', 0, { conversions: 0 }),
    make('google_ads', 'g-inactive-spend', 'PAUSED', 40, { conversions: 4 }),
    make('google_ads', 'g-inactive-zero', 'REMOVED', 0, { conversions: 0 }),
    make('google_ads', 'g-unknown', 'MYSTERY', 30, { conversions: 3 }),
    make('google_ads', 'g-null-spend', 'ENABLED', null, { conversions: 8 }),
    make('google_ads', 'g-null-conversions', 'ENABLED', 5, { conversions: null }),
  ];
  const mRows = [
    make('meta_ads', 'm-active-spend', 'ACTIVE', 60, { conversions: 999, purchases: 6, leads: 5 }),
    make('meta_ads', 'm-active-zero', 'ACTIVE', 0, { conversions: 0, purchases: 0, leads: 0 }),
    make('meta_ads', 'm-inactive-spend', 'WITH_ISSUES', 20, { conversions: 99, purchases: 2, leads: 1 }),
    make('meta_ads', 'm-inactive-zero', 'CAMPAIGN_PAUSED', 0, { conversions: 0, purchases: 0, leads: 0 }),
    make('meta_ads', 'm-unknown', 'MYSTERY', 15, { conversions: 88, purchases: 1, leads: 2 }),
    make('meta_ads', 'm-null-spend', 'ACTIVE', null, { conversions: 99, purchases: 4, leads: 4 }),
    make('meta_ads', 'm-null-result', 'ACTIVE', 5, { conversions: 9, purchases: null, leads: null }),
  ];

  const analyze = (status: 'all' | 'active' | 'inactive' | 'unknown', spend: 'all' | 'with_spend' | 'without_spend', metaMode: 'purchase' | 'lead' = 'purchase') =>
    analyzeConversions([...gRows, ...mRows], dates, { status, spend, metaMode, availablePlatforms: [...available] });

  it('sums Google conversions and canonical Meta purchases without adding generic Meta conversions', () => {
    const result = analyze('all', 'all');
    expect(result.google_ads.conversions).toBe(25);
    expect(result.meta_ads.conversions).toBe(13);
    expect(result.conversionsTotal).toBe(38);
    // Campaign totals include only available results, while the daily chart
    // stays unavailable for that day because one campaign's purchase is null.
    expect(result.meta_ads.daily[0].conversions).toBeNull();
  });
  it('uses Meta leads for lead-mode clients and never substitutes purchases', () => {
    const result = analyze('all', 'all', 'lead');
    expect(result.meta_ads.conversions).toBe(12);
    expect(result.conversionsTotal).toBe(37);
  });
  it('calcula CPA parcial solo con gasto y resultado Google cuando Meta leads no existe', () => {
    const result = analyzeConversions([
      make('google_ads', 'g-known', 'ENABLED', 1000, { conversions: 10 }),
      make('meta_ads', 'm-lead-missing', 'ACTIVE', 2000, { conversions: 500, purchases: 20, leads: null }),
    ], dates, { status: 'all', spend: 'all', metaMode: 'lead', availablePlatforms: [...available] });
    expect(result.google_ads.conversions).toBe(10);
    expect(result.meta_ads.conversions).toBeNull();
    expect(result.conversionsTotal).toBe(10);
    expect(result.partialTotal).toBe(true);
    expect(result.cpa).toBe(100);
    expect(result.cpaSources).toEqual(['google_ads']);
    expect(result.cpaPartial).toBe(true);
  });
  it('filters source rows before totals, daily chart and CPA calculations', () => {
    const activeSpend = analyze('active', 'with_spend');
    expect(activeSpend.google_ads.conversions).toBe(10);
    expect(activeSpend.meta_ads.conversions).toBe(6);
    expect(activeSpend.conversionsTotal).toBe(16);
    expect(activeSpend.totalSpend).toBe(170);
    expect(activeSpend.cpa).toBeNull();
    expect(activeSpend.partialTotal).toBe(true);
    expect(activeSpend.google_ads.daily[0]).toEqual({ date: dates[0], conversions: null, spend: 105 });
    expect(activeSpend.meta_ads.daily[0]).toEqual({ date: dates[0], conversions: null, spend: 65 });
    expect(activeSpend.visibleRows.map((row) => row.campaign_id)).toEqual(['g-active-spend', 'g-null-conversions', 'm-active-spend', 'm-null-result']);
  });
  it('returns correct independent active-zero, inactive-spend, inactive-zero, and unknown filters', () => {
    expect(analyze('active', 'without_spend').conversionsTotal).toBe(0);
    expect(analyze('inactive', 'with_spend').conversionsTotal).toBe(6);
    expect(analyze('inactive', 'without_spend').conversionsTotal).toBe(0);
    expect(analyze('unknown', 'all').conversionsTotal).toBe(4);
  });
  it('keeps null spend out of specific spend filters and preserves unavailable results as null', () => {
    const all = analyze('all', 'all');
    expect(all.visibleRows.some((row) => row.campaign_id === 'g-null-spend')).toBe(true);
    expect(analyze('all', 'with_spend').visibleRows.some((row) => row.spend == null)).toBe(false);
    expect(analyze('all', 'without_spend').visibleRows.some((row) => row.spend == null)).toBe(false);
    expect(analyze('all', 'with_spend').google_ads.conversions).toBe(17);
    expect(analyze('all', 'with_spend').google_ads.partialConversions).toBe(true);
    expect(analyze('all', 'with_spend').meta_ads.conversions).toBe(9);
    expect(analyze('all', 'with_spend').meta_ads.partialConversions).toBe(true);
  });
  it('does not combine spend or CPA across currencies, while conversion-event totals remain valid', () => {
    const result = analyzeConversions([
      make('google_ads', 'g-mxn', 'ENABLED', 100, { conversions: 10 }, 'MXN'),
      make('meta_ads', 'm-usd', 'ACTIVE', 20, { purchases: 2 }, 'USD'),
    ], dates, { status: 'all', spend: 'all', metaMode: 'purchase', availablePlatforms: [...available] });
    expect(result.conversionsTotal).toBe(12);
    expect(result.totalSpend).toBeNull();
    expect(result.cpa).toBeNull();
    expect(result.mixedCurrency).toBe(true);
  });
  it('distinguishes an unavailable platform from a measured zero', () => {
    const unavailable = analyzeConversions([], dates, { status: 'all', spend: 'all', metaMode: 'lead', availablePlatforms: ['google_ads'] });
    expect(unavailable.google_ads.conversions).toBe(0);
    expect(unavailable.meta_ads.conversions).toBeNull();
  });

  it('rellena solo con cero medido las campañas sin breakdown diario y conserva null como ausente', () => {
    const googleZero = make('google_ads', 'g-zero-without-daily', 'ENABLED', 0, { conversions: 0 });
    delete googleZero.daily;
    const googleNull = make('google_ads', 'g-null-without-daily', 'ENABLED', null, { conversions: null });
    delete googleNull.daily;
    const result = analyzeConversions([googleZero, googleNull], dates, {
      status: 'all', spend: 'all', metaMode: 'lead', availablePlatforms: ['google_ads'],
    });
    // spend=null row keeps platform totals incomplete; daily measured-zero
    // campaign itself can still be represented, while missing source data is
    // not silently replaced with zero.
    expect(result.google_ads.daily[0].conversions).toBeNull();
    expect(result.google_ads.daily[0].spend).toBeNull();
    const onlyZero = analyzeConversions([googleZero], dates, {
      status: 'all', spend: 'all', metaMode: 'lead', availablePlatforms: ['google_ads'],
    });
    expect(onlyZero.google_ads.daily[0]).toEqual({ date: dates[0], conversions: 0, spend: 0 });
  });
});

describe('totales agregados sin filtros', () => {
  it('usa el total canónico del endpoint aunque la mayoría de filas omita purchases', () => {
    const rows = [
      make('google_ads', 'g', 'ENABLED', 100, { conversions: 10 }),
      make('meta_ads', 'm-purchase', 'ACTIVE', 200, { conversions: 999, purchases: 28, leads: null }),
      make('meta_ads', 'm-no-action', 'ACTIVE', 0, { conversions: 0, purchases: null, leads: null }),
    ];
    const rowAnalysis = analyzeConversions(rows, dates, {
      status: 'all', spend: 'all', metaMode: 'purchase', availablePlatforms: [...available],
    });
    expect(rowAnalysis.meta_ads.partialConversions).toBe(true);
    const result = useAggregateConversionTotals(rowAnalysis, {
      google_ads: { available: true, currency: 'MXN', totals: { conversions: 10, purchases: null, leads: null, spend: 100 }, daily: [{ date: dates[0], conversions: 10, purchases: null, leads: null, spend: 100 }] },
      meta_ads: { available: true, currency: 'MXN', totals: { conversions: 28, purchases: 28, leads: null, spend: 200 }, daily: [{ date: dates[0], conversions: 28, purchases: 28, leads: null, spend: 200 }] },
    }, dates, 'purchase');
    expect(result.google_ads.conversions).toBe(10);
    expect(result.meta_ads.conversions).toBe(28);
    expect(result.conversionsTotal).toBe(38);
    expect(result.partialTotal).toBe(false);
    expect(result.cpa).toBeCloseTo(300 / 38, 8);
    expect(result.cpaPartial).toBe(false);
    expect(result.meta_ads.daily[0].conversions).toBe(28);
  });

  it('mantiene Purchase null como no disponible y calcula CPA parcial con fuentes compatibles', () => {
    const rowAnalysis = analyzeConversions([
      make('google_ads', 'g', 'ENABLED', 100, { conversions: 10 }),
      make('meta_ads', 'm', 'ACTIVE', 200, { conversions: 0, purchases: null, leads: 0 }),
    ], dates, { status: 'all', spend: 'all', metaMode: 'purchase', availablePlatforms: [...available] });
    const result = useAggregateConversionTotals(rowAnalysis, {
      google_ads: { available: true, currency: 'MXN', totals: { conversions: 10, purchases: null, leads: null, spend: 100 }, daily: [] },
      meta_ads: { available: true, currency: 'MXN', totals: { conversions: 0, purchases: null, leads: 0, spend: 200 }, daily: [] },
    }, dates, 'purchase');
    expect(result.meta_ads.conversions).toBeNull();
    expect(result.conversionsTotal).toBe(10);
    expect(result.partialTotal).toBe(true);
    expect(result.cpa).toBe(10);
    expect(result.cpaPartial).toBe(true);
  });
});
