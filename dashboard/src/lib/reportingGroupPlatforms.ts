import type { ReportingGroup, ReportingPlatform } from '../types/reporting';

export const REPORTING_PLATFORMS: readonly ReportingPlatform[] = [
  'google_ads', 'meta_ads', 'spotify_ads', 'pinterest_ads',
];

/** UI compatibility only; the backend resolves the same legacy fallback for reporting. */
export function resolvedGroupPlatforms(group: ReportingGroup): Record<ReportingPlatform, boolean> {
  return Object.fromEntries(REPORTING_PLATFORMS.map((platform) => [
    platform,
    group.enabled_platforms?.[platform] ?? group.assignments.some((assignment) => assignment.platform === platform),
  ])) as Record<ReportingPlatform, boolean>;
}

export function setGroupPlatformEnabled(
  groups: ReportingGroup[], groupId: string, platform: ReportingPlatform, enabled: boolean,
): ReportingGroup[] {
  return groups.map((group) => group.id === groupId
    ? { ...group, enabled_platforms: { ...resolvedGroupPlatforms(group), [platform]: enabled } }
    : group);
}
