/** IDs registrados con autorización para métricas de valor en la interfaz. */
export const VALUE_METRICS_ENABLED_CLIENTS = new Set([
  'cirque-du-soleil-joya',
  'jungala',
  'ssiento',
  'blumia',
]);

export const valueMetricsEnabled = (clientId: string): boolean =>
  VALUE_METRICS_ENABLED_CLIENTS.has(clientId);

export type MetaResultMode = 'purchase' | 'lead';

/** Resultado principal de Meta por client_id; los clientes no listados conservan leads. */
export const META_RESULT_MODE: Readonly<Record<string, MetaResultMode>> = {
  'cirque-du-soleil-joya': 'purchase',
  jungala: 'purchase',
  ssiento: 'purchase',
  blumia: 'purchase',
};

export const metaResultMode = (clientId: string): MetaResultMode =>
  META_RESULT_MODE[clientId] ?? 'lead';
