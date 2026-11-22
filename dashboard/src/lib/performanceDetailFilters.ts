import type { PerformanceDetailRow } from '../types/reporting';

export type DetailStatusFilter = 'all' | 'active' | 'inactive' | 'unknown';
export type DetailSpendFilter = 'all' | 'with_spend' | 'without_spend';

const INACTIVE = new Set([
  'PAUSED', 'REMOVED', 'ARCHIVED', 'DELETED', 'CAMPAIGN_PAUSED', 'ADSET_PAUSED',
  'WITH_ISSUES', 'DISABLED', 'CANCELED', 'CANCELLED', 'ENDED', 'EXPIRED',
  'PENDING_REVIEW', 'DISAPPROVED', 'PREAPPROVED', 'IN_PROCESS',
]);

export function detailStatus(row: PerformanceDetailRow): 'active' | 'inactive' | 'unknown' {
  const value = (row.platform === 'meta_ads'
    ? row.effective_status ?? row.status ?? row.campaign_effective_status ?? row.campaign_status
    : row.status ?? row.campaign_status)?.trim().toUpperCase();
  if (value === 'ACTIVE' || value === 'ENABLED') return 'active';
  if (value && INACTIVE.has(value)) return 'inactive';
  return 'unknown';
}

export function filterDetailRows(
  rows: PerformanceDetailRow[],
  status: DetailStatusFilter,
  spend: DetailSpendFilter,
): PerformanceDetailRow[] {
  return rows.filter((row) => {
    if (status !== 'all' && detailStatus(row) !== status) return false;
    if (spend === 'with_spend' && !(row.spend != null && row.spend > 0)) return false;
    if (spend === 'without_spend' && row.spend !== 0) return false;
    return true;
  });
}
