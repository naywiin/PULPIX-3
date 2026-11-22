import type { Totals } from '../lib/metrics';
import type { MetricRecord } from './metrics';
import type { DateRange } from './report';

/** De dónde vienen los datos que se están viendo. Nunca se mezclan en una misma vista. */
export type DataSource = 'real' | 'mock';

export type BlockStatus = 'ok' | 'partial' | 'not_linked' | 'not_configured' | 'auth_error' | 'permission_error' | 'provider_error' | 'error';

export type DashboardPlatform = 'google_ads' | 'meta_ads' | 'spotify_ads' | 'pinterest_ads' | 'ga4';

export interface DailyAdsPoint {
  date: string;
  spend: number;
  conversions: number | null;
  leads?: number | null;
  purchases?: number | null;
  conversion_value: number | null;
  clicks: number;
  impressions: number;
}

export interface AdsPeriod {
  totals: Totals;
  campaigns: MetricRecord[];
  adSets: MetricRecord[];
  ads?: MetricRecord[];
  daily: DailyAdsPoint[];
  /** Cuántas cuentas de origen aportaron datos. */
  sourcesCount?: number;
  /** Meta: el reach viene sumado entre cuentas y NO está deduplicado. */
  reachIsSum?: boolean;
  /** Pinterest: checkout ads reports (null means unavailable, not zero). */
  checkouts?: number | null;
  pinterestTotals?: PinterestMetrics;
  pinterestDaily?: (PinterestMetrics & { date: string })[];
}

export interface PinterestMetrics {
  spend: number | null;
  impressions: number | null;
  clicks: number | null;
  ctr: number | null;
  cpc: number | null;
  checkouts: number | null;
  cost_per_checkout: number | null;
  conversion_value: number | null;
  roas: number | null;
  currency: string | null;
}

export interface Ga4Metrics {
  /** activeUsers. NO es deduplicable entre propiedades: al consolidar se suma. */
  users: number;
  sessions: number;
  /** null cuando la fuente no lo entrega (p. ej. datos mock). */
  engaged_sessions: number | null;
  engagement_rate: number | null;
  key_events: number;
  revenue: number;
}

export interface Ga4Row extends Ga4Metrics {
  name: string;
}

export interface Ga4Period {
  totals: Ga4Metrics;
  daily: (Ga4Metrics & { date: string })[];
  by_source_medium: Ga4Row[];
  by_campaign: Ga4Row[];
  by_landing_page: Ga4Row[];
  /** Cuántas propiedades aportaron datos a este periodo (consolidado). */
  propertiesCount?: number;
}

/** Una propiedad GA4 del cliente, con sus propios datos y su propio estado. */
export interface Ga4Property {
  propertyId: string;
  propertyName: string | null;
  currency: string | null;
  status: BlockStatus;
  message?: string;
  detail?: string;
  current: Ga4Period | null;
  previous: Ga4Period | null;
}

/** Aviso de un bloque: una fuente que falló, o algo global (monedas mezcladas). */
export interface BlockWarning {
  /** GA4: la propiedad afectada. null/ausente = aviso del bloque entero. */
  propertyId?: string | null;
  /** Google Ads / Meta: la cuenta de origen afectada. */
  accountId?: string | null;
  status: string;
  message: string;
  detail?: string;
}

/** Nombre histórico; los avisos son los mismos en todas las plataformas. */
export type Ga4Warning = BlockWarning;

export interface PlatformBlock<P> {
  platform: DashboardPlatform;
  label: string;
  status: BlockStatus;
  source: DataSource;
  accountId: string | null;
  currency: string;
  message?: string;
  detail?: string;
  /** Cuentas anunciantes vs. MCC secundarios (managers). */
  summary?: { total: number; managers: number; advertisers: number };
  /** GA4: cuenta → propiedades, tal como las agrupa la Admin API. */
  groups?: { account_id: string; account_name: string | null; properties: DiscoveredAccount[] }[];
  /** Cuántas cuentas dejó fuera la lista de exclusión (sin nombres ni patrones). */
  excluded?: { count: number };
  /** GA4: todas las propiedades seleccionadas del cliente. */
  propertyIds?: string[];
  /** GA4: datos de cada propiedad por separado (vista "Por propiedad"). */
  properties?: Ga4Property[];
  /** GA4: propiedades que fallaron y avisos del consolidado. */
  warnings?: BlockWarning[];
  /** GA4: las propiedades no comparten moneda, el revenue sumado es heterogéneo. */
  mixedCurrency?: boolean;
  /** Ads: resultado por cuenta de origen (una caída no tumba el bloque). */
  sources?: AdSourceResult[];
  current: P | null;
  previous: P | null;
}

export interface DashboardData {
  source: DataSource;
  clientId: string;
  range: DateRange;
  compareRange: DateRange | null;
  generatedAt: string;
  google_ads: PlatformBlock<AdsPeriod>;
  meta_ads: PlatformBlock<AdsPeriod>;
  spotify_ads: PlatformBlock<AdsPeriod>;
  pinterest_ads?: PlatformBlock<AdsPeriod>;
  ga4: PlatformBlock<Ga4Period>;
}

/** Cómo se atribuye una campaña asignada desde la cuenta de otro cliente. */
export type Allocation = 'exclusive' | 'shared';

interface AdSourceBase {
  /** Vacío = todas las campañas elegibles de la cuenta. */
  campaign_ids: string[];
  allocation: Allocation;
}

export interface GoogleAdsSource extends AdSourceBase {
  customer_id: string;
  login_customer_id: string | null;
}

/** Conjuntos de anuncios concretos dentro de una campaña (nivel más específico). */
export interface AdSetSelection {
  campaign_id: string;
  adset_ids: string[];
  allocation: Allocation;
}

export interface MetaAdsSource extends AdSourceBase {
  ad_account_id: string;
  /** Seleccionar ad sets NO equivale a seleccionar la campaña entera. */
  adset_selections?: AdSetSelection[];
}

export interface SpotifyAdsSource {
  business_id: string;
  ad_account_id: string;
}

export interface PinterestAdsSource extends AdSourceBase {
  source_id: string;
  ad_account_id: string;
  display_name?: string | null;
  currency?: string | null;
  status?: string | null;
}

/** Nivel al que está definida una fuente de Meta. */
export type SourceLevel = 'account' | 'campaign' | 'adset' | 'mixed';

/** Un conjunto de anuncios del catálogo de una campaña. */
export interface DiscoveredAdSet {
  platform: 'meta_ads';
  ad_account_id: string;
  account_name: string | null;
  campaign_id: string;
  campaign_name: string | null;
  adset_id: string;
  adset_name: string | null;
  status: string;
  effective_status?: string;
  optimization_goal?: string | null;
  billing_event?: string | null;
  assigned_to: { id: string; name: string; allocation: Allocation } | null;
}

export interface AdSetCatalog {
  platform: 'meta_ads';
  account_id: string;
  campaign_id: string;
  status: 'ok' | 'not_configured' | 'auth_error' | 'permission_error' | 'provider_error' | 'error' | 'unsupported';
  message?: string;
  detail?: string;
  count?: number;
  adsets: DiscoveredAdSet[];
}

export type AdSource = GoogleAdsSource | MetaAdsSource;

/** ID de cuenta de una fuente, sea de la plataforma que sea. */
export function sourceAccountId(s: AdSource): string {
  return 'customer_id' in s ? s.customer_id : s.ad_account_id;
}

/** Solo una fuente sin campañas NI ad sets representa la cuenta completa. */
export function isWholeAccountSource(s: AdSource): boolean {
  return s.campaign_ids.length === 0 &&
    (!('adset_selections' in s) || (s.adset_selections?.length ?? 0) === 0);
}

/** Una campaña del catálogo de una cuenta (para elegirla en Settings). */
export interface DiscoveredCampaign {
  platform: DashboardPlatform;
  campaign_id: string;
  campaign_name: string;
  status: string;
  effective_status?: string;
  advertising_channel_type?: string;
  objective?: string;
  account_name: string | null;
  currency?: string | null;
  customer_id?: string;
  ad_account_id?: string;
  /** Cliente que ya la tiene asignada, si alguno. */
  assigned_to: { id: string; name: string; allocation: Allocation } | null;
}

export interface CampaignCatalog {
  platform: DashboardPlatform;
  account_id: string;
  status: 'ok' | 'not_configured' | 'error' | 'unsupported';
  message?: string;
  detail?: string;
  count?: number;
  campaigns: DiscoveredCampaign[];
}

/** Resultado de UNA cuenta de origen dentro del bloque de una plataforma. */
export interface AdSourceResult {
  accountId: string;
  accountName: string | null;
  campaignIds: string[];
  allocation: Allocation;
  /** account | campaign | adset | mixed. */
  level?: SourceLevel;
  adsetSelections?: AdSetSelection[];
  /** Universo realmente consultado tras el recorte jerárquico. */
  effective?: { campaigns: string[] | null; adsets: Record<string, string[]> };
  status: BlockStatus | 'empty';
  message?: string;
  detail?: string;
  currency: string | null;
}

/** Cliente tal como lo expone el backend (registro central). Solo IDs, nunca secretos. */
export interface ApiClient {
  id: string;
  name: string;
  slug: string;
  logo: string | null;
  active: boolean;
  report_category?: 'ENTRETENIMIENTO' | 'B2B / CORPORATIVO' | 'ECOMMERCE' | 'GENERAL' | null;
  report_position?: number | null;
  /** Espejo de la primera fuente (cuenta principal). Canónico: *_sources. */
  google_ads_customer_id: string | null;
  google_ads_login_customer_id: string | null;
  meta_ad_account_id: string | null;
  google_ads_sources: GoogleAdsSource[];
  meta_ads_sources: MetaAdsSource[];
  spotify_ads_sources?: SpotifyAdsSource[];
  spotify_source_id?: string | null;
  spotify_linked?: boolean;
  spotify_status?: 'linked' | 'not_configured';
  pinterest_ads_sources?: PinterestAdsSource[];
  pinterest_source_id?: string | null;
  pinterest_linked?: boolean;
  pinterest_status?: 'linked' | 'not_configured' | 'auth_error' | 'permission_error' | 'provider_error';
  /** Campo histórico: espejo de la primera de ga4_property_ids. */
  ga4_property_id: string | null;
  /** Todas las propiedades GA4 del cliente. Fuente de verdad. */
  ga4_property_ids: string[];
  ga4_account_id: string | null;
  currency: string;
  timezone: string;
  brand_color: string | null;
  notes: string | null;
  /** Familias de métrica ocultas para este cliente (ver config/metricVisibility). */
  hidden_metrics: string[];
}

export interface DiscoveredAccount {
  platform: 'google_ads' | 'meta_ads' | 'spotify_ads' | 'pinterest_ads' | 'ga4';
  id: string;
  name: string | null;
  currency: string | null;
  timezone: string | null;
  status: string;
  account_type: string;
  login_customer_id?: string | null;
  access?: string;
  /** Google Ads: 1 = cliente directo del MCC raíz, ≥ 2 = indirecto (vía MCC secundario). */
  level?: number;
  test_account?: boolean;
  hidden?: boolean;
  account_name?: string;
  /** GA4: la propiedad y la cuenta que la contiene (Admin API ListAccountSummaries). */
  account_id?: string;
  property_id?: string;
  property_name?: string;
  property_resource?: string;
  property_type?: string;
  business?: string | null;
  business_id?: string | null;
  error?: string;
  assigned_to: { id: string; name: string } | null;
  reserved_for?: { id: string; name: string } | null;
}

export interface DiscoveryResult {
  platform: 'google_ads' | 'meta_ads' | 'spotify_ads' | 'pinterest_ads' | 'ga4';
  status: 'ok' | 'not_configured' | 'auth_error' | 'permission_error' | 'provider_error' | 'error';
  message?: string;
  detail?: string;
  /** Cuentas anunciantes vs. MCC secundarios (managers). */
  summary?: { total: number; managers: number; advertisers: number };
  /** GA4: cuenta → propiedades, tal como las agrupa la Admin API. */
  groups?: { account_id: string; account_name: string | null; properties: DiscoveredAccount[] }[];
  /** Cuántas cuentas dejó fuera la lista de exclusión (sin nombres ni patrones). */
  excluded?: { count: number };
  fetched_at?: string;
  accounts: DiscoveredAccount[];
}
