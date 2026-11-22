import type { ClientConfig } from './clients';
import type { MetricDef } from './metrics';
import { valueMetricsEnabled } from './valueMetrics';

/**
 * Alias → familia canónica. Cualquier variante visual de una métrica (por
 * plataforma o por nombre comercial) se clasifica bajo la misma familia, así
 * `hiddenMetrics: ['roas']` agrupa los alias históricos de ROAS,
 * la columna "value" de conversion_value, etc. sin enumerarlas todas.
 *
 * Debe reflejar `METRIC_FAMILY_ALIASES` de `backend/app/metrics.py`: ambos
 * lados colapsan las mismas variantes a la misma familia.
 *
 * Para ocultar una métrica nueva en el futuro basta con añadir sus variantes
 * aquí; no hace falta tocar ningún componente.
 */
const FAMILY_ALIASES: Record<string, string> = {
  roas: 'roas',
  meta_roas: 'roas',
  google_roas: 'roas',
  gads_roas: 'roas',
  blended_roas: 'roas',
  purchase_roas: 'roas',
  conversion_value: 'conversion_value',
  purchase_value: 'conversion_value',
  purchase_conversion_value: 'conversion_value',
  value: 'conversion_value',
  cpa: 'cpa',
  cost_per_conversion: 'cpa',
  cost_per_purchase: 'cpa',
  cpp: 'cpa',
};

/** Familia canónica de una métrica, a partir de su `MetricDef` o de una clave suelta. */
export function metricFamily(metric: string | MetricDef): string {
  const raw = typeof metric === 'string' ? metric : metric.key;
  const norm = raw.trim().toLowerCase().replace(/[\s-]+/g, '_');
  return FAMILY_ALIASES[norm] ?? norm;
}

type HiddenSource = Pick<ClientConfig, 'id' | 'hiddenMetrics'> | null | undefined;

/** true si el cliente tiene oculta esta métrica (por familia), cualquiera que sea la variante. */
export function isMetricHidden(client: HiddenSource, metric: string | MetricDef): boolean {
  const family = metricFamily(metric);
  if ((family === 'roas' || family === 'conversion_value') && !valueMetricsEnabled(client?.id ?? '')) return true;
  const hidden = client?.hiddenMetrics;
  if (!hidden || hidden.length === 0) return false;
  // La política de valor por ID prevalece sobre las preferencias históricas.
  return family !== 'roas' && family !== 'conversion_value' && hidden.some((h) => metricFamily(h) === family);
}

export function isMetricVisible(client: HiddenSource, metric: string | MetricDef): boolean {
  return !isMetricHidden(client, metric);
}

/** Filtra una lista de KPIs, quitando las que el cliente tiene ocultas. Punto
 * único usado por Overview, Google Ads y Meta Ads para las tarjetas de KPI. */
export function visibleMetrics<T extends MetricDef>(client: HiddenSource, metrics: T[]): T[] {
  return metrics.filter((m) => isMetricVisible(client, m));
}

/** Filtra columnas de tabla cuyo `key` coincide con una métrica oculta (ej.
 * la columna "roas" de una DataTable). Mismo mecanismo que `visibleMetrics`,
 * para no duplicar la lógica de ocultamiento entre KPIs y tablas. */
export function filterHiddenColumns<T extends { key: string }>(client: HiddenSource, columns: T[]): T[] {
  return columns.filter((c) => isMetricVisible(client, c.key));
}
