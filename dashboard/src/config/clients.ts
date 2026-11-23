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
  /** Color de marca para el avatar/logo generado. */
  brandColor: string;
  /** Ruta opcional a un logo propio en /public. */
  logoUrl?: string;
  currency: string;
  locale: string;
  timezone: string;
  sources: Partial<Record<Platform, DataSourceConfig>>;
  /**
   * Familias de métrica que este cliente no debe ver en el dashboard (ej.
   * `['roas']`). Ausente o vacío = todo visible.
   */
  hiddenMetrics?: string[];
}

/**
 * Registro de clientes de demostración (modo MOCK local sin backend).
 * En producción cada usuario gestiona sus propios clientes desde su cuenta.
 */
export const CLIENTS: ClientConfig[] = [
  {
    id: 'cliente-demo',
    name: 'Pulpix Demo',
    reportName: 'Pulpix · Reporte de Performance',
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
  {
    id: 'cliente-sin-conectar',
    name: 'Cliente sin conectar',
    reportName: 'Pulpix · Reporte de Performance',
    brandColor: '#1d4ed8',
    currency: 'MXN',
    locale: 'es-MX',
    timezone: 'America/Mexico_City',
    sources: {},
  },
];

export function getClient(id: string): ClientConfig {
  return CLIENTS.find((c) => c.id === id) ?? CLIENTS[0];
}
