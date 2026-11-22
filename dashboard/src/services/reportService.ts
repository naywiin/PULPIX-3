import { ADAPTERS } from '../adapters';
import type { ClientConfig } from '../config/clients';
import type { Platform } from '../types/metrics';
import type { DateRange, PeriodData, PlatformReport } from '../types/report';

/** Capa intermedia entre la UI y los adapters: arma periodo actual + comparado. */
export async function fetchPlatformReport(
  client: ClientConfig,
  platform: Platform,
  range: DateRange,
  compareRange: DateRange | null,
  today: string,
  signal?: AbortSignal,
): Promise<PlatformReport | null> {
  const source = client.sources[platform];
  if (!source) return null; // plataforma no configurada → empty state

  const adapter = ADAPTERS[platform];
  if (!adapter) return null; // Spotify Ads no se simula ni se consulta desde el navegador.
  const loadPeriod = async (r: DateRange): Promise<PeriodData> => {
    const base = { clientId: client.id, source, range: r, today, signal };
    const [campaigns, adSets] = await Promise.all([
      adapter.getPerformance({ ...base, level: 'campaign' }),
      adapter.supportedLevels.includes('ad_set') ? adapter.getPerformance({ ...base, level: 'ad_set' }) : Promise.resolve([]),
    ]);
    return { campaigns, adSets };
  };

  const [current, previous] = await Promise.all([
    loadPeriod(range),
    compareRange ? loadPeriod(compareRange) : Promise.resolve(null),
  ]);
  return { platform, current, previous };
}
