import type { Platform } from '../types/metrics';

export type SourceMode = 'mock' | 'api';

export interface DataSourceConfig {
  /** ID de cuenta en la plataforma. En modo mock es un identificador local. */
  accountId: string;
  mode: SourceMode;
}

export interface ClientConfig {
  id: string;
  name: string;
  reportName: string;
  /** Color de marca para el avatar/logo generado. No usamos logos de terceros. */
  brandColor: string;
  /** Ruta opcional a un logo propio en /public (ej. "/logos/blumia.svg"). */
  logoUrl?: string;
  currency: string;
  locale: string;
  timezone: string;
  sources: Partial<Record<Platform, DataSourceConfig>>;
  /**
   * Familias de métrica que este cliente no debe ver en el dashboard (ej.
   * `['roas']`). Ausente o vacío = todo visible. Nunca se lee directamente:
   * usa `isMetricVisible` / `visibleMetrics` de config/metricVisibility.
   */
  hiddenMetrics?: string[];
}

/**
 * Registro de clientes de demostración (modo MOCK). Para agregar uno nuevo basta
 * con añadir una entrada aquí y, si usa datos mock, un dataset en src/mock/.
 * IMPORTANTE: aquí solo van IDs de cuenta, nunca tokens ni secretos, y ningún
 * nombre que esté en config/exclusions.ts.
 */
export const CLIENTS: ClientConfig[] = [
  {
    id: 'blumia',
    name: 'Blumia',
    reportName: 'Reporte de Performance · Paid Media',
    brandColor: '#0f766e',
    currency: 'MXN',
    locale: 'es-MX',
    timezone: 'America/Mexico_City',
    sources: {
      google_ads: { accountId: 'mock-gads-blumia', mode: 'mock' },
      meta_ads: { accountId: 'mock-meta-blumia', mode: 'mock' },
      google_analytics: { accountId: 'mock-ga4-blumia', mode: 'mock' },
    },
  },
  {
    id: 'cliente-sin-conectar',
    name: 'Cliente sin conectar',
    reportName: 'Reporte de Performance · Paid Media',
    brandColor: '#1d4ed8',
    currency: 'MXN',
    locale: 'es-MX',
    timezone: 'America/Mexico_City',
    // Sin fuentes conectadas todavía: el dashboard muestra el empty state.
    sources: {},
  },
  {
    id: 'cliente-demo',
    name: 'Cliente Demo',
    reportName: 'Reporte de Performance · Paid Media',
    brandColor: '#7c3aed',
    currency: 'MXN',
    locale: 'es-MX',
    timezone: 'America/Mexico_City',
    sources: {
      google_ads: { accountId: 'mock-gads-demo', mode: 'mock' },
      meta_ads: { accountId: 'mock-meta-demo', mode: 'mock' },
      google_analytics: { accountId: 'mock-ga4-demo', mode: 'mock' },
    },
  },
];

export function getClient(id: string): ClientConfig {
  return CLIENTS.find((c) => c.id === id) ?? CLIENTS[0];
}
