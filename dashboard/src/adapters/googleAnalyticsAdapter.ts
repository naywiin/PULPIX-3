import type { MetricRecord } from '../types/metrics';
import { mockLatency, queryMock } from '../mock';
import { AdapterNotConnectedError, type PerformanceQuery, type PlatformAdapter } from './types';

/**
 * Google Analytics 4 (Data API).
 *
 * Conexión real (pendiente de autorización): implementar en backend
 * properties/{property_id}:runReport con
 *   dimensions = sessionSource, sessionMedium (o sessionCampaignName)
 *   metrics    = sessions, totalUsers, keyEvents, totalRevenue
 *
 * Mapeo al modelo común: campaign_name = "source / medium", clicks = sessions,
 * reach = totalUsers, conversions = keyEvents, conversion_value = totalRevenue.
 */
async function fetchFromApi(q: PerformanceQuery): Promise<MetricRecord[]> {
  void q;
  throw new AdapterNotConnectedError('Google Analytics');
}

export const googleAnalyticsAdapter: PlatformAdapter = {
  platform: 'google_analytics',
  label: 'Google Analytics',
  supportedLevels: ['campaign'],
  async getPerformance(q) {
    if (q.source.mode === 'api') return fetchFromApi(q);
    await mockLatency();
    return queryMock(q.clientId, 'google_analytics', q.range, q.level, q.today);
  },
};
