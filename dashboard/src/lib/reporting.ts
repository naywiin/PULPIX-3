import type { ReportingClient, BudgetPlatform, ReportPeriodValidation } from '../types/reporting';

export const BUDGETS_UPDATED_EVENT = 'mp:budgets-updated';

export const BUDGET_PLATFORM_LABELS: Record<BudgetPlatform, string> = {
  google_ads: 'Google Ads',
  meta_ads: 'Meta Ads',
  spotify_ads: 'Spotify Ads',
  pinterest_ads: 'Pinterest Ads',
};

export const MONTH_LABELS = [
  'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
  'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
];

/** Deriva plataformas exclusivamente de las asignaciones canónicas del registro. */
export function assignedBudgetPlatforms(client: ReportingClient): BudgetPlatform[] {
  const platforms: BudgetPlatform[] = [];
  if (client.google_ads_sources.length) platforms.push('google_ads');
  if (client.meta_ads_sources.length) platforms.push('meta_ads');
  if (client.spotify_ads_sources?.length) platforms.push('spotify_ads');
  if (client.pinterest_ads_sources?.length) platforms.push('pinterest_ads');
  return platforms;
}

export function validateReportPeriod(from: string, to: string): ReportPeriodValidation {
  if (!from || !to) return { valid: false, error: 'Desde y Hasta son obligatorios.' };
  if (from > to) return { valid: false, error: 'Desde debe ser anterior o igual a Hasta.' };
  return { valid: true };
}

export function defaultReportFilename(from: string, to: string): string {
  return `Reporte_Performance_${from}_al_${to}.xlsx`;
}
