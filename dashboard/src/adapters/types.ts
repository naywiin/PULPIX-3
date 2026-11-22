import type { DataSourceConfig } from '../config/clients';
import type { EntityLevel, MetricRecord, Platform } from '../types/metrics';
import type { DateRange } from '../types/report';

export interface PerformanceQuery {
  clientId: string;
  source: DataSourceConfig;
  range: DateRange;
  level: EntityLevel;
  /** Fecha máxima con datos (hoy en la zona del cliente). */
  today: string;
  signal?: AbortSignal;
}

/**
 * Contrato que implementa cada plataforma. La UI solo conoce esta interfaz.
 * Un adapter debe devolver registros YA agregados para el rango solicitado
 * (las APIs calculan reach/frequency de forma nativa por periodo).
 */
export interface PlatformAdapter {
  platform: Platform;
  label: string;
  supportedLevels: EntityLevel[];
  getPerformance(q: PerformanceQuery): Promise<MetricRecord[]>;
}

export class AdapterNotConnectedError extends Error {
  constructor(platform: string) {
    super(`La conexión real con ${platform} aún no está habilitada. Usa mode: 'mock' en src/config/clients.ts.`);
    this.name = 'AdapterNotConnectedError';
  }
}
