import type { ApiClient } from './dashboard';

export type BudgetPlatform = 'google_ads' | 'meta_ads' | 'spotify_ads' | 'pinterest_ads';

export interface MonthlyBudget {
  client_id: string;
  platform: BudgetPlatform;
  year: number;
  month: number;
  /** null = presupuesto legacy/general sin grupo. */
  reporting_group_id?: string | null;
  account_id?: string | null;
  campaign_id?: string | null;
  budget: number;
  currency: string;
}

export type ReportingPlatform = BudgetPlatform;
export type CampaignReportingPlatform = ReportingPlatform;
export interface CampaignGroupAssignment { platform: ReportingPlatform; account_id: string; campaign_id: string }
export interface MonthlyCampaignAssignment extends CampaignGroupAssignment { reporting_group_id: string | null }
export interface ReportingGroup {
  id: string; client_id?: string; name: string; type: string; active: boolean;
  enabled_platforms?: Partial<Record<ReportingPlatform, boolean>> | null;
  assignments: CampaignGroupAssignment[];
}
export type ReportingGroupSnapshotState = 'configured' | 'not_configured' | 'missing_group_configuration';
export interface ReportingGroupSnapshotResult {
  client_id: string;
  year: number;
  month: number;
  schema_version: number;
  state: ReportingGroupSnapshotState;
  groups: ReportingGroup[];
  assignments: MonthlyCampaignAssignment[];
  has_previous_snapshot: boolean;
  can_initialize_from_current: boolean;
}

export interface DownloadedReport {
  blob: Blob;
  filename: string;
}

export interface ReportPeriodValidation {
  valid: boolean;
  error?: string;
}

export type PacingStatus = 'green' | 'yellow' | 'red' | 'missing_budget' | 'currency_mismatch' | 'unavailable';

export interface PerformanceRecord {
  level: 'month_platform' | 'platform_total' | 'period_total';
  reporting_group_id?: string | null;
  reporting_group_name?: string | null;
  reporting_group_type?: string | null;
  client_id: string;
  client_name: string | null;
  platform: BudgetPlatform | null;
  platforms: BudgetPlatform[];
  month: number | null;
  year: number | null;
  month_label: string | null;
  from: string;
  to: string;
  currency: string | null;
  account_ids: string[];
  status: 'ok' | 'partial';
  budget_captured: boolean;
  budget_complete?: boolean;
  budget_partial?: boolean;
  budget_source?: 'group' | 'campaign' | 'mixed' | 'unavailable';
  budget_currency_compatible: boolean;
  currency_compatible: boolean;
  monthly_budget: number | null;
  applicable_days: number;
  days_in_month: number;
  observed_days: number;
  spend: number | null;
  conversions: number | null;
  purchases?: number | null;
  leads: number | null;
  conversion_value: number | null;
  clicks: number | null;
  impressions: number | null;
  reach: number | null;
  budget_period: number | null;
  budget_difference: number | null;
  expected_spend: number | null;
  pacing_difference: number | null;
  budget_consumed: number | null;
  daily_conversions: number | null;
  projected_conversions: number | null;
  cpa: number | null;
  cpl: number | null;
  cost_per_purchase?: number | null;
  roas: number | null;
  ticket_average: number | null;
  total_events?: number | null;
  total_events_available?: boolean;
  total_events_partial?: boolean;
  total_event_sources?: string[];
  total_cpa?: number | null;
  total_cpa_partial?: boolean;
  total_cpa_sources?: string[];
  total_cpc?: number | null;
  conversion_value_partial?: boolean;
  conversion_value_sources?: string[];
  roas_partial?: boolean;
  roas_sources?: string[];
  projected_sales_value?: number | null;
  cpc: number | null;
  ctr: number | null;
  frequency: number | null;
  pacing_status: PacingStatus;
  reach_is_unique: boolean;
  reach_is_sum: boolean;
}

export interface PerformanceSummary {
  client_id: string;
  client_name: string;
  from: string;
  to: string;
  currency: string;
  monthly: PerformanceRecord[];
  grouping_active?: boolean;
  grouped_monthly?: PerformanceRecord[];
  platform_totals: PerformanceRecord[];
  period_total: PerformanceRecord | null;
  pacing_thresholds: { green_max: number; yellow_max: number };
}

export type PerformanceDetailLevel = 'campaign' | 'ad';
export type PerformanceDetailStatus = 'all' | 'active' | 'inactive';
export type PerformanceDetailSpend = 'all' | 'with_spend' | 'without_spend';
export type PerformanceDetailPlatform = 'google_ads' | 'meta_ads' | 'spotify_ads' | 'pinterest_ads';

export interface PerformanceDetailRow {
  level: PerformanceDetailLevel;
  platform: PerformanceDetailPlatform;
  source_account_id: string;
  currency?: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  campaign_status?: string | null;
  campaign_effective_status?: string | null;
  ad_group_id?: string | null;
  ad_group_name?: string | null;
  ad_set_id?: string | null;
  ad_set_name?: string | null;
  ad_id?: string | null;
  ad_name?: string | null;
  ad_type?: string | null;
  status?: string | null;
  effective_status?: string | null;
  status_label: string;
  spend: number | null;
  impressions: number | null;
  clicks: number | null;
  conversions: number | null;
  purchases?: number | null;
  leads: number | null;
  reach?: number | null;
  conversion_value: number | null;
  value_available?: boolean;
  cpc: number | null;
  ctr: number | null;
  cpa: number | null;
  cpl: number | null;
  cost_per_purchase?: number | null;
  roas: number | null;
  objective?: string | null;
  reporting_groups?: { year: number; month: number; id: string | null; name: string; type: string }[];
  /** Métricas diarias de esta campaña, usadas para análisis agregados filtrables. */
  daily?: {
    date: string;
    spend: number | null;
    conversions: number | null;
    purchases?: number | null;
    leads: number | null;
  }[];
}

export interface PerformanceDetailResponse {
  client_id: string;
  platform: PerformanceDetailPlatform;
  level: PerformanceDetailLevel;
  status_filter: PerformanceDetailStatus;
  spend_filter: PerformanceDetailSpend;
  range: { start: string; end: string };
  count: number;
  currency?: string | null;
  mixed_currency: boolean;
  rows: PerformanceDetailRow[];
  totals: {
    spend: number | null;
    impressions: number | null;
    clicks: number | null;
    conversions: number | null;
    purchases?: number | null;
    leads: number | null;
    conversion_value: number | null;
    cpc: number | null;
    ctr: number | null;
    cpa: number | null;
    cpl: number | null;
    cost_per_purchase?: number | null;
    roas: number | null;
  };
  cached: boolean;
  grouping_active: boolean;
}

export type ReportingClient = Pick<
  ApiClient,
  'id' | 'name' | 'active' | 'currency' | 'google_ads_sources' | 'meta_ads_sources' | 'spotify_ads_sources' | 'pinterest_ads_sources'
>;
