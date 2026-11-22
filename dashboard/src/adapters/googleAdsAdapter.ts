import type { MetricRecord } from '../types/metrics';
import { mockLatency, queryMock } from '../mock';
import { AdapterNotConnectedError, type PerformanceQuery, type PlatformAdapter } from './types';

/**
 * Google Ads.
 *
 * Conexión real (pendiente de autorización): NO llamar a la API desde el navegador.
 * Implementar un endpoint backend (ej. GET /api/google-ads/performance) que use
 * las credenciales del servidor y ejecute GAQL, por ejemplo:
 *
 *   SELECT campaign.id, campaign.name, metrics.impressions, metrics.clicks,
 *          metrics.cost_micros, metrics.conversions, metrics.conversions_value
 *   FROM campaign
 *   WHERE segments.date BETWEEN '{start}' AND '{end}'
 *
 * y normalizar: spend = cost_micros / 1e6, conversion_value = conversions_value.
 */
async function fetchFromApi(q: PerformanceQuery): Promise<MetricRecord[]> {
  void q;
  throw new AdapterNotConnectedError('Google Ads');
}

export const googleAdsAdapter: PlatformAdapter = {
  platform: 'google_ads',
  label: 'Google Ads',
  supportedLevels: ['campaign'],
  async getPerformance(q) {
    if (q.source.mode === 'api') return fetchFromApi(q);
    await mockLatency();
    return queryMock(q.clientId, 'google_ads', q.range, q.level, q.today);
  },
};
