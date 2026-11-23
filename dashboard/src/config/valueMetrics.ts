/**
 * Configuración de métricas de valor y modo de resultado en Meta.
 *
 * En Pulpix no hay restricciones por client_id de terceros: cualquier cliente
 * puede ver métricas de valor (ROAS / valor de conversión) a menos que el
 * usuario oculte `roas` en la configuración del propio cliente.
 */
export const VALUE_METRICS_ENABLED_CLIENTS = new Set<string>();

export const valueMetricsEnabled = (_clientId: string): boolean => true;

export type MetaResultMode = 'purchase' | 'lead';

export const META_RESULT_MODE: Readonly<Record<string, MetaResultMode>> = {};

export const metaResultMode = (clientId: string, preferred?: MetaResultMode): MetaResultMode =>
  preferred ?? META_RESULT_MODE[clientId] ?? 'lead';
