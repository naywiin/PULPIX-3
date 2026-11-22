import type { MetricRecord, Platform } from './metrics';

export interface DateRange {
  /** YYYY-MM-DD inclusive */
  start: string;
  /** YYYY-MM-DD inclusive */
  end: string;
}

export type RangePreset =
  | 'today'
  | 'yesterday'
  | 'last_7'
  | 'last_30'
  | 'this_month'
  | 'last_month'
  | 'this_year'
  | 'custom';

export type ComparePreset = 'none' | 'previous_period' | 'previous_month' | 'previous_year' | 'custom';

export interface DateSelection {
  preset: RangePreset;
  range: DateRange;
  compare: ComparePreset;
  compareRange: DateRange | null;
}

export interface PeriodData {
  campaigns: MetricRecord[];
  adSets: MetricRecord[];
}

export interface PlatformReport {
  platform: Platform;
  current: PeriodData;
  previous: PeriodData | null;
}
