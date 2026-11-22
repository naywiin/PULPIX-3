import { describe, expect, it } from 'vitest';
import type { ClientConfig } from './clients';
import { METRICS } from './metrics';
import { filterHiddenColumns, visibleMetrics } from './metricVisibility';
import { META_RESULT_MODE, VALUE_METRICS_ENABLED_CLIENTS, metaResultMode, valueMetricsEnabled } from './valueMetrics';
import { formatValue } from '../lib/format';

const client = (id: string): ClientConfig => ({
  id, name: id, reportName: '', brandColor: '#000', currency: 'MXN', locale: 'es-MX',
  timezone: 'America/Mexico_City', sources: {}, hiddenMetrics: ['roas'],
});

const meta = [METRICS.meta_spend, METRICS.meta_leads, METRICS.meta_cost_per_result,
  METRICS.cpc, METRICS.impressions, METRICS.clicks, METRICS.ctr, METRICS.meta_reach,
  METRICS.meta_conversion_value, METRICS.meta_roas];
const google = [METRICS.gads_cost, METRICS.gads_conversions, METRICS.gads_cpa,
  METRICS.gads_avg_cpc, METRICS.impressions, METRICS.clicks, METRICS.ctr,
  METRICS.conversion_rate, METRICS.gads_conv_value, METRICS.gads_roas];

describe('política de métricas de valor por client_id', () => {
  it('elige la semántica Meta por ID canónico sin ampliar la allowlist de valor', () => {
    expect(META_RESULT_MODE).toEqual({
      'cirque-du-soleil-joya': 'purchase', jungala: 'purchase', ssiento: 'purchase', blumia: 'purchase',
    });
    expect(metaResultMode('cirque-du-soleil-joya')).toBe('purchase');
    expect(metaResultMode('jungala')).toBe('purchase');
    expect(metaResultMode('ssiento')).toBe('purchase');
    expect(metaResultMode('blumia')).toBe('purchase');
    expect(metaResultMode('clarvi')).toBe('lead');
    expect(metaResultMode('vidanta-resorts')).toBe('lead');
  });
  for (const id of ['cirque-du-soleil-joya', 'jungala', 'ssiento', 'blumia']) {
    it(`${id}: muestra ROAS y Valor de conversión`, () => {
      expect(valueMetricsEnabled(id)).toBe(true);
      for (const metrics of [meta, google]) {
        const keys = visibleMetrics(client(id), metrics).map((m) => m.key);
        expect(keys).toContain('roas');
        expect(keys).toContain('conversion_value');
      }
    });
  }
  it('cliente normal: oculta cards y columnas de valor', () => {
    const normal = client('clarvi');
    for (const metrics of [meta, google]) {
      const keys = visibleMetrics(normal, metrics).map((m) => m.key);
      expect(keys).not.toContain('roas');
      expect(keys).not.toContain('conversion_value');
    }
    expect(filterHiddenColumns(normal, [{ key: 'spend' }, { key: 'conversion_value' }, { key: 'roas' }]))
      .toEqual([{ key: 'spend' }]);
  });
  it('la allowlist contiene exclusivamente los cuatro client_id confirmados', () => {
    expect([...VALUE_METRICS_ENABLED_CLIENTS]).toEqual([
      'cirque-du-soleil-joya', 'jungala', 'ssiento', 'blumia',
    ]);
    expect(valueMetricsEnabled('vidanta')).toBe(false);
  });
  it('otro cliente relacionado con Vidanta no hereda la política por nombre', () => {
    const related = client('vidanta-resorts');
    related.name = 'Vidanta Resorts';
    for (const metrics of [meta, google]) {
      const keys = visibleMetrics(related, metrics).map((m) => m.key);
      expect(keys).not.toContain('roas');
      expect(keys).not.toContain('conversion_value');
    }
  });
});

describe('semántica visible', () => {
  it('Meta conserva su catálogo de resultados y costo por resultado', () => {
    expect(meta.map((m) => m.label)).toEqual([
      'Gasto', 'Clientes potenciales', 'Costo por resultado', 'CPC', 'Impresiones',
      'Clics', 'CTR', 'Alcance', 'Valor de conversión', 'ROAS',
    ]);
    expect(1000 / 10).toBe(100);
    expect(formatValue(null, 'currency', { currency: 'MXN', locale: 'es-MX' })).toBe('—');
  });
  it('Google presenta conversiones y CPA sin Alcance', () => {
    expect(google.map((m) => m.label)).toEqual([
      'Gasto', 'Conversiones', 'CPA · Costo por adquisición', 'CPC', 'Impresiones',
      'Clics', 'CTR', 'Conversion Rate', 'Valor de conversión', 'ROAS',
    ]);
    expect(google.some((m) => m.key === 'reach')).toBe(false);
  });
});
