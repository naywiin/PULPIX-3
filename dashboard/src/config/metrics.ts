/**
 * Definición de cada métrica: cómo se formatea y en qué dirección es "mejora".
 *  - higher  → subir es bueno (ROAS, conversiones)
 *  - lower   → bajar es bueno (CPA, CPC)
 *  - neutral → solo se muestra el cambio, sin juicio (gasto)
 */
export type MetricFormat = 'currency' | 'number' | 'decimal' | 'percent' | 'ratio';
export type MetricDirection = 'higher' | 'lower' | 'neutral';

export interface MetricDef {
  key: string;
  label: string;
  format: MetricFormat;
  direction: MetricDirection;
}

const def = (key: string, label: string, format: MetricFormat, direction: MetricDirection): MetricDef => ({
  key,
  label,
  format,
  direction,
});

export const METRICS = {
  impressions: def('impressions', 'Impresiones', 'number', 'higher'),
  reach: def('reach', 'Alcance', 'number', 'higher'),
  clicks: def('clicks', 'Clics', 'number', 'higher'),
  inline_link_clicks: def('inline_link_clicks', 'Inline Link Clicks', 'number', 'higher'),
  ctr: def('ctr', 'CTR', 'percent', 'higher'),
  cpc: def('cpc', 'CPC', 'currency', 'lower'),
  cpm: def('cpm', 'CPM', 'currency', 'lower'),
  frequency: def('frequency', 'Frequency', 'decimal', 'neutral'),
  conversion_rate: def('conversion_rate', 'Conversion Rate', 'percent', 'higher'),

  // Google Ads
  gads_cost: def('spend', 'Gasto', 'currency', 'neutral'),
  gads_avg_cpc: def('cpc', 'CPC', 'currency', 'lower'),
  gads_cpa: def('cpa', 'CPA · Costo por adquisición', 'currency', 'lower'),
  gads_conversions: def('conversions', 'Conversiones', 'decimal', 'higher'),
  gads_conv_value: def('conversion_value', 'Valor de conversión', 'currency', 'higher'),
  gads_roas: def('roas', 'ROAS', 'ratio', 'higher'),

  // Meta Ads
  meta_spend: def('spend', 'Gasto', 'currency', 'neutral'),
  meta_leads: def('leads', 'Clientes potenciales', 'decimal', 'higher'),
  meta_cost_per_result: def('cost_per_result', 'Costo por resultado', 'currency', 'lower'),
  meta_purchases: def('purchases', 'Compras', 'decimal', 'higher'),
  meta_cost_per_purchase: def('cost_per_purchase', 'Costo por resultado', 'currency', 'lower'),
  meta_conversion_value: def('conversion_value', 'Valor de conversión', 'currency', 'higher'),
  meta_roas: def('roas', 'ROAS', 'ratio', 'higher'),
  meta_reach: def('reach', 'Alcance', 'number', 'higher'),
  /**
   * Con varias cuentas el alcance se suma y NO está deduplicado: una misma
   * persona puede estar incluida en más de una cuenta publicitaria. Se etiqueta
   * para no presentarlo como usuarios únicos globales.
   */
  meta_reach_sum: def('reach', 'Alcance (suma de cuentas)', 'number', 'higher'),

  // Spotify Ads
  spotify_spend: def('spend', 'Spend', 'currency', 'neutral'),
  spotify_impressions: def('impressions', 'Impressions', 'number', 'higher'),
  spotify_clicks: def('clicks', 'Clicks', 'number', 'higher'),
  spotify_reach: def('reach', 'Reach', 'number', 'higher'),
  spotify_frequency: def('frequency', 'Frequency', 'decimal', 'neutral'),
  spotify_ctr: def('ctr', 'CTR', 'percent', 'higher'),
  spotify_cpm: def('cpm', 'CPM', 'currency', 'lower'),

  // Overview combinado (Google Ads + Meta Ads)
  total_spend: def('spend', 'Gasto total', 'currency', 'neutral'),
  total_conversions: def('conversions', 'Conversiones totales', 'decimal', 'higher'),
  total_cpa: def('cpa', 'CPA total', 'currency', 'lower'),
  total_cpc: def('cpc', 'CPC total', 'currency', 'lower'),
  total_value: def('conversion_value', 'Valor de conversión', 'currency', 'higher'),
  blended_roas: def('roas', 'Blended ROAS', 'ratio', 'higher'),

  // Google Analytics 4
  ga_users: def('users', 'Users', 'number', 'higher'),
  ga_users_sum: def('users', 'Usuarios activos (suma de propiedades)', 'number', 'higher'),
  ga_sessions: def('sessions', 'Sessions', 'number', 'higher'),
  ga_engaged: def('engaged_sessions', 'Engaged Sessions', 'number', 'higher'),
  ga_engagement_rate: def('engagement_rate', 'Engagement Rate', 'percent', 'higher'),
  ga_key_events: def('key_events', 'Conversions / Key Events', 'decimal', 'higher'),
  ga_revenue: def('revenue', 'Revenue', 'currency', 'higher'),
} satisfies Record<string, MetricDef>;
