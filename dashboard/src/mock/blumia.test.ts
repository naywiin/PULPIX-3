import { describe, expect, it } from 'vitest';
import { computeChange, sumRecords } from '../lib/metrics';
import { queryMock } from '.';

const SEP = { start: '2026-09-01', end: '2026-09-24' };
const TODAY = '2026-09-24';
const r2 = (n: number) => Math.round(n * 100) / 100;

describe('mock Blumia reproduce los valores de referencia (1–24 sep 2026)', () => {
  it('Google Ads', () => {
    const rows = queryMock('blumia', 'google_ads', SEP, 'campaign', TODAY);
    const t = sumRecords(rows);
    expect(t.spend).toBe(3613.39);
    expect(t.conversions).toBe(19);
    expect(t.conversion_value).toBe(8800);
    expect(r2(t.cpa)).toBe(190.18);
    expect(r2(t.roas)).toBe(2.44);
    const search = rows.find((r) => r.campaign_name.startsWith('SEARCH'))!;
    expect(search).toMatchObject({ impressions: 1025, clicks: 209, spend: 1269.7, conversions: 4, conversion_value: 1740 });
    expect(r2(search.ctr * 100)).toBe(20.39);
    expect(r2(search.cpc)).toBe(6.08);
    const pmax = rows.find((r) => r.campaign_name.startsWith('PMAX'))!;
    expect(pmax).toMatchObject({ impressions: 3958, clicks: 553, spend: 2343.69, conversions: 15, conversion_value: 7060 });
    expect(r2(pmax.ctr * 100)).toBe(13.97);
    expect(r2(pmax.cpc)).toBe(4.24);
  });

  it('Meta Ads', () => {
    const t = sumRecords(queryMock('blumia', 'meta_ads', SEP, 'campaign', TODAY));
    expect(t.spend).toBe(10028.83);
    expect(t.conversions).toBe(16);
    expect(r2(t.cpa)).toBe(626.8);
    expect(r2(t.roas)).toBe(0.73);
    const adSets = sumRecords(queryMock('blumia', 'meta_ads', SEP, 'ad_set', TODAY));
    expect(adSets.spend).toBe(10028.83);
  });

  it('genera datos distintos para el año anterior y los deltas respetan la dirección', () => {
    const prev = sumRecords(queryMock('blumia', 'google_ads', { start: '2025-09-01', end: '2025-09-24' }, 'campaign', TODAY));
    expect(prev.spend).toBeGreaterThan(0);
    expect(prev.spend).not.toBe(3613.39);
    expect(computeChange(100, 120, 'lower').sentiment).toBe('positive');
    expect(computeChange(2.44, 2.0, 'higher').sentiment).toBe('positive');
    expect(computeChange(3613.39, 3200.1, 'neutral').sentiment).toBe('neutral');
  });

  it('Cliente Demo tiene historial propio 2025/2026 en ambas plataformas', () => {
    for (const platform of ['google_ads', 'meta_ads'] as const) {
      const current = sumRecords(queryMock('cliente-demo', platform, SEP, 'campaign', TODAY));
      const previous = sumRecords(queryMock('cliente-demo', platform, { start: '2025-09-01', end: '2025-09-24' }, 'campaign', TODAY));
      expect(current.spend).toBeGreaterThan(0);
      expect(current.conversions).toBeGreaterThan(0);
      expect(previous.spend).toBeGreaterThan(0);
      expect(previous.spend).not.toBe(current.spend);
    }
  });
});
