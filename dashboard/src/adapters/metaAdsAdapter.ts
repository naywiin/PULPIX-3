import type { MetricRecord } from '../types/metrics';
import { mockLatency, queryMock } from '../mock';
import { AdapterNotConnectedError, type PerformanceQuery, type PlatformAdapter } from './types';

/**
 * Meta Marketing API.
 *
 * Conexión real (pendiente de autorización): implementar en backend
 * GET /act_{account_id}/insights con
 *   level = campaign | adset
 *   time_range = {"since": start, "until": end}
 *   fields = campaign_id,campaign_name,adset_id,adset_name,objective,impressions,
 *            reach,frequency,clicks,inline_link_clicks,spend,actions,action_values
 * y normalizar leads desde acciones de lead; las acciones de compra se conservan
 * por separado para compatibilidad del reporting histórico.
 */
async function fetchFromApi(q: PerformanceQuery): Promise<MetricRecord[]> {
  void q;
  throw new AdapterNotConnectedError('Meta Ads');
}

export const metaAdsAdapter: PlatformAdapter = {
  platform: 'meta_ads',
  label: 'Meta Ads',
  supportedLevels: ['campaign', 'ad_set'],
  async getPerformance(q) {
    if (q.source.mode === 'api') return fetchFromApi(q);
    await mockLatency();
    return queryMock(q.clientId, 'meta_ads', q.range, q.level, q.today);
  },
};
