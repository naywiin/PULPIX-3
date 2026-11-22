/**
 * Modelo común de métricas. Todos los adapters (Google Ads, Meta, GA4) deben
 * normalizar su respuesta a este formato; la UI nunca ve payloads crudos de API.
 */
export type Platform = 'google_ads' | 'meta_ads' | 'spotify_ads' | 'pinterest_ads' | 'google_analytics';

export type EntityLevel = 'campaign' | 'ad_set' | 'ad';

export interface MetricRecord {
  client_id: string;
  platform: Platform;
  account_id: string;
  campaign_id: string;
  campaign_name: string;
  status?: string | null;
  effective_status?: string | null;
  /** Inicio del periodo (YYYY-MM-DD). Para registros diarios es el día. */
  date: string;
  /** Fin del periodo (YYYY-MM-DD), igual a `date` en registros diarios. */
  date_end: string;

  // Métricas base (aditivas, excepto reach)
  impressions: number;
  /** Alcance único. NO es aditivo: debe venir calculado por la API para el periodo. */
  reach: number;
  clicks: number;
  spend: number;
  conversions: number;
  conversion_value: number;
  value_available?: boolean;
  /** Acciones de lead reportadas por Meta; null cuando el proveedor no las entrega. */
  leads?: number | null;
  /** Acción real de compra reportada por Meta; null si no la informa. */
  purchases?: number | null;
  cost_per_result?: number | null;
  cost_per_purchase?: number | null;
  checkouts?: number | null;
  pinterestSpend?: number | null;
  pinterestImpressions?: number | null;
  pinterestClicks?: number | null;
  pinterestCtr?: number | null;
  pinterestCpc?: number | null;
  pinterestConversionValue?: number | null;
  pinterestRoas?: number | null;

  // Métricas derivadas (siempre recalculadas con `deriveMetrics`)
  ctr: number;
  cpc: number;
  cpm: number;
  cpa: number;
  roas: number;

  // Campos opcionales por plataforma / nivel
  ad_set_id?: string;
  ad_set_name?: string;
  ad_id?: string;
  ad_name?: string;
  business_id?: string;
  /** Cuenta de la que procede la campaña (multi-cuenta por cliente). */
  source_account_id?: string;
  source_account_name?: string;
  /** La campaña se consume solo por algunos de sus conjuntos de anuncios. */
  partial?: boolean;
  selected_adsets?: number;
  objective?: CampaignObjective;
  inline_link_clicks?: number;
  frequency?: number;
  conversion_rate?: number;
  completes?: number;
  starts?: number;
  first_quartiles?: number;
  midpoints?: number;
  third_quartiles?: number;
  video_views?: number;
  video_expands?: number;
  video_expand_rate?: number;
}

export type CampaignObjective = 'SALES' | 'LEADS' | 'TRAFFIC' | 'AWARENESS' | 'ENGAGEMENT' | 'OTHER';

/** Métricas base que un adapter debe entregar; las derivadas se calculan después. */
export type BaseMetrics = Pick<
  MetricRecord,
  'impressions' | 'reach' | 'clicks' | 'spend' | 'conversions' | 'conversion_value'
> & { inline_link_clicks?: number };
