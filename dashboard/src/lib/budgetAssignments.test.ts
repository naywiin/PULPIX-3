import { describe, expect, it } from 'vitest';
import { completeCampaignAssignments } from './budgetAssignments';

describe('completeCampaignAssignments', () => {
  it('preserves saved assignments outside the visible filter and adds the full catalog', () => {
    const hidden = { platform: 'google_ads' as const, account_id: 'acct', campaign_id: 'hidden', reporting_group_id: 'traffic' };
    const visible = { platform: 'google_ads' as const, account_id: 'acct', campaign_id: 'visible' };
    expect(completeCampaignAssignments([hidden], [visible])).toEqual([
      hidden,
      { ...visible, reporting_group_id: null },
    ]);
  });

  it('does not duplicate a campaign already in the saved configuration', () => {
    const saved = { platform: 'meta_ads' as const, account_id: 'meta', campaign_id: 'c1', reporting_group_id: 'conversion' };
    expect(completeCampaignAssignments([saved], [saved])).toEqual([saved]);
  });

  it('drops catalog-only display fields from persisted assignments', () => {
    const campaign = {
      platform: 'meta_ads' as const,
      account_id: 'meta',
      campaign_id: 'c2',
      key: 'meta_ads:meta:c2',
      name: 'TEST campaign',
      objective: 'OUTCOME_LEADS',
    };
    expect(completeCampaignAssignments([], [campaign])).toEqual([{
      platform: 'meta_ads', account_id: 'meta', campaign_id: 'c2', reporting_group_id: null,
    }]);
  });
});
