import { describe, expect, it } from 'vitest';
import { buildSalesProjections, type ProjectionSource } from './salesProjection';

const source = (key: 'google_ads' | 'meta_ads', currency: string | null, values: (number | null)[], available = true): ProjectionSource => ({
  key, label: key === 'google_ads' ? 'Google Ads' : 'Meta Ads', currency, available,
  daily: values.map((conversion_value, index) => ({ date: `2026-02-${String(index + 1).padStart(2, '0')}`, spend: 0, conversions: null, conversion_value, clicks: 0, impressions: 0 })),
});

describe('proyección mensual del valor atribuido', () => {
  it('proyecta un mes parcial usando solo días observados y suma Google + Meta en moneda común', () => {
    const result = buildSalesProjections({ start: '2026-02-01', end: '2026-02-02' }, [source('google_ads', 'MXN', [100, 200]), source('meta_ads', 'MXN', [50, 50])], '2026-02-02')[0];
    expect(result.daysInMonth).toBe(28);
    expect(result.observedBySource.google_ads).toBe(300);
    expect(result.projectedBySource.google_ads).toBe(4200);
    expect(result.observedTotal).toBe(400);
    expect(result.projectedTotal).toBe(5600);
    expect(result.series.some((item) => item.dash)).toBe(true);
  });
  it('mes completo pasado muestra valor real final y no proyección', () => {
    const result = buildSalesProjections({ start: '2026-02-01', end: '2026-02-28' }, [source('google_ads', 'MXN', Array(28).fill(10))], '2026-03-10')[0];
    expect(result.complete).toBe(true);
    expect(result.observedTotal).toBe(280);
    expect(result.projectedTotal).toBe(280);
    expect(result.series.some((item) => item.dash)).toBe(false);
  });
  it('no inventa cierre ni extrapola un mes pasado cuando faltan días de valores', () => {
    const result = buildSalesProjections({ start: '2026-02-01', end: '2026-02-28' }, [source('google_ads', 'MXN', [100])], '2026-03-10')[0];
    expect(result.closed).toBe(true);
    expect(result.complete).toBe(false);
    expect(result.observedBySource.google_ads).toBe(100);
    expect(result.projectedBySource.google_ads).toBeNull();
    expect(result.projectedTotal).toBeNull();
    expect(result.series.some((item) => item.dash)).toBe(false);
  });
  it.each([['2026-04-01', 30], ['2026-01-01', 31], ['2026-02-01', 28]])('maneja días del mes %s', (start, days) => {
    const end = `${start.slice(0, 8)}02`;
    expect(buildSalesProjections({ start, end }, [source('google_ads', 'MXN', [0, null])], end)[0].daysInMonth).toBe(days);
  });
  it('no convierte null en cero, conserva cero medido y distingue moneda distinta', () => {
    const missing = buildSalesProjections({ start: '2026-02-01', end: '2026-02-02' }, [source('google_ads', 'MXN', [null, null])], '2026-02-02')[0];
    expect(missing.observedBySource.google_ads).toBeNull();
    const zero = buildSalesProjections({ start: '2026-02-01', end: '2026-02-01' }, [source('google_ads', 'MXN', [0])], '2026-02-01')[0];
    expect(zero.observedBySource.google_ads).toBe(0);
    const mixed = buildSalesProjections({ start: '2026-02-01', end: '2026-02-01' }, [source('google_ads', 'MXN', [10]), source('meta_ads', 'USD', [5])], '2026-02-01')[0];
    expect(mixed.observedTotal).toBeNull();
    expect(mixed.mixedCurrency).toBe(true);
    expect(mixed.projectedTotal).toBeNull();
  });
  it('permite valor solo Google, solo Meta y total parcial si una fuente falta', () => {
    const googleOnly = buildSalesProjections({ start: '2026-02-01', end: '2026-02-01' }, [source('google_ads', 'MXN', [8]), source('meta_ads', 'MXN', [null], false)], '2026-02-01')[0];
    expect(googleOnly.observedTotal).toBe(8);
    expect(googleOnly.totalPartial).toBe(true);
    const metaOnly = buildSalesProjections({ start: '2026-02-01', end: '2026-02-01' }, [source('meta_ads', 'MXN', [9])], '2026-02-01')[0];
    expect(metaOnly.observedBySource.meta_ads).toBe(9);
  });
  it('separa periodos de dos meses', () => {
    const data = source('google_ads', 'MXN', []);
    data.daily.push({ date: '2026-02-28', spend: 0, conversions: null, conversion_value: 10, clicks: 0, impressions: 0 });
    data.daily.push({ date: '2026-03-01', spend: 0, conversions: null, conversion_value: 20, clicks: 0, impressions: 0 });
    const result = buildSalesProjections({ start: '2026-02-28', end: '2026-03-01' }, [data], '2026-03-01');
    expect(result.map((item) => item.key)).toEqual(['2026-02', '2026-03']);
    expect(result[0].observedBySource.google_ads).toBe(10);
    expect(result[1].observedBySource.google_ads).toBe(20);
    expect(result.every((item) => item.series.every((series) => series.values.every((value) => value == null || Number.isFinite(value))))).toBe(true);
  });
});
