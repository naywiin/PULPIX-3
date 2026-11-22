import type { MonthlyCampaignAssignment } from '../types/reporting';

export interface BudgetCampaignRef {
  platform: MonthlyCampaignAssignment['platform'];
  account_id: string;
  campaign_id: string;
}

export function campaignAssignmentKey(item: BudgetCampaignRef): string {
  return `${item.platform}:${item.account_id}:${item.campaign_id}`;
}

/** Add catalog campaigns without dropping assignments outside the visible filter. */
export function completeCampaignAssignments(
  saved: MonthlyCampaignAssignment[],
  catalog: BudgetCampaignRef[],
): MonthlyCampaignAssignment[] {
  const byKey = new Map(saved.map((item) => [campaignAssignmentKey(item), item] as const));
  for (const campaign of catalog) {
    const key = campaignAssignmentKey(campaign);
    if (!byKey.has(key)) byKey.set(key, {
      platform: campaign.platform,
      account_id: campaign.account_id,
      campaign_id: campaign.campaign_id,
      reporting_group_id: null,
    });
  }
  return [...byKey.values()];
}
