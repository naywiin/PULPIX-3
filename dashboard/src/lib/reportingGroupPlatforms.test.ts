import { describe, expect, it } from 'vitest';
import type { ReportingGroup } from '../types/reporting';
import { resolvedGroupPlatforms, setGroupPlatformEnabled } from './reportingGroupPlatforms';

const sample = (): ReportingGroup[] => [{
  id: 'traffic', client_id: 'blumia', name: 'Tráfico', type: 'traffic', active: true,
  assignments: [
    { platform: 'google_ads', account_id: 'g', campaign_id: 'g1' },
    { platform: 'meta_ads', account_id: 'm', campaign_id: 'm1' },
  ],
}];

describe('reporting group platform switches', () => {
  it('backfills old snapshots from assignments without enabling unassigned Spotify or Pinterest', () => {
    expect(resolvedGroupPlatforms(sample()[0])).toEqual({ google_ads: true, meta_ads: true, spotify_ads: false, pinterest_ads: false });
  });

  it('supports Google only, Meta only, both, and neither without deleting assignments', () => {
    const original = sample();
    for (const enabled_platforms of [
      { google_ads: true, meta_ads: false, spotify_ads: false, pinterest_ads: false },
      { google_ads: false, meta_ads: true, spotify_ads: false, pinterest_ads: false },
      { google_ads: true, meta_ads: true, spotify_ads: false, pinterest_ads: false },
      { google_ads: false, meta_ads: false, spotify_ads: false, pinterest_ads: false },
    ]) {
      const updated = [{ ...original[0], enabled_platforms }];
      expect(resolvedGroupPlatforms(updated[0])).toEqual(enabled_platforms);
      expect(updated[0].assignments).toEqual(original[0].assignments);
    }
  });

  it('turning one platform off keeps its campaign assignment for later reactivation', () => {
    const changed = setGroupPlatformEnabled(sample(), 'traffic', 'meta_ads', false);
    expect(changed[0].enabled_platforms?.meta_ads).toBe(false);
    expect(changed[0].assignments).toHaveLength(2);
    const restored = setGroupPlatformEnabled(changed, 'traffic', 'meta_ads', true);
    expect(restored[0].assignments).toHaveLength(2);
    expect(restored[0].enabled_platforms?.meta_ads).toBe(true);
  });

  it('supports Pinterest group switch without changing Google or Meta assignments', () => {
    const changed = setGroupPlatformEnabled(sample(), 'traffic', 'pinterest_ads', true);
    expect(changed[0].enabled_platforms?.pinterest_ads).toBe(true);
    expect(changed[0].assignments).toEqual(sample()[0].assignments);
  });
});
