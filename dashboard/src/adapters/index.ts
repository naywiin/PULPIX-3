import type { Platform } from '../types/metrics';
import { googleAdsAdapter } from './googleAdsAdapter';
import { googleAnalyticsAdapter } from './googleAnalyticsAdapter';
import { metaAdsAdapter } from './metaAdsAdapter';
import type { PlatformAdapter } from './types';

// Spotify solo se consume desde el backend real; no tiene adapter/mock en navegador.
export const ADAPTERS: Partial<Record<Platform, PlatformAdapter>> = {
  google_ads: googleAdsAdapter,
  meta_ads: metaAdsAdapter,
  google_analytics: googleAnalyticsAdapter,
};

export { googleAdsAdapter, metaAdsAdapter, googleAnalyticsAdapter };
export * from './types';
