import type { CampaignObjective, Platform } from '../types/metrics';
import type { DateRange } from '../types/report';
import type { MockTotals } from './series';

export interface MockAdSet {
  id: string;
  name: string;
  totals: MockTotals;
}

export interface MockCampaign {
  id: string;
  name: string;
  objective: CampaignObjective;
  /** Para Google Ads se usa un solo "ad set" implícito con los totales de la campaña. */
  adSets: MockAdSet[];
  activeFrom?: string;
}

export interface MockAccount {
  accountId: string;
  anchor: DateRange;
  /** Desde cuándo existe historial mock. */
  dataStart: string;
  campaigns: MockCampaign[];
}

export type MockClientData = Partial<Record<Platform, MockAccount>>;
