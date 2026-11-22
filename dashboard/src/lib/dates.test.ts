import { describe, expect, it } from 'vitest';
import { compareRangeFor, presetRange } from './dates';

const TODAY = '2026-09-24';

describe('presets', () => {
  it('calcula rangos rápidos', () => {
    expect(presetRange('today', TODAY)).toEqual({ start: TODAY, end: TODAY });
    expect(presetRange('yesterday', TODAY)).toEqual({ start: '2026-09-23', end: '2026-09-23' });
    expect(presetRange('last_7', TODAY)).toEqual({ start: '2026-09-17', end: '2026-09-23' });
    expect(presetRange('last_30', TODAY)).toEqual({ start: '2026-08-25', end: '2026-09-23' });
    expect(presetRange('this_month', TODAY)).toEqual({ start: '2026-09-01', end: TODAY });
    expect(presetRange('last_month', TODAY)).toEqual({ start: '2026-08-01', end: '2026-08-31' });
    expect(presetRange('this_year', TODAY)).toEqual({ start: '2026-01-01', end: TODAY });
    expect(presetRange('last_month', '2026-01-15')).toEqual({ start: '2025-12-01', end: '2025-12-31' });
  });
});

describe('comparaciones', () => {
  const sep = { start: '2026-09-01', end: '2026-09-24' };
  it('año anterior usa exactamente las mismas fechas', () => {
    expect(compareRangeFor('previous_year', sep)).toEqual({ start: '2025-09-01', end: '2025-09-24' });
    expect(compareRangeFor('previous_year', { start: '2028-02-29', end: '2028-02-29' })).toEqual({ start: '2027-02-28', end: '2027-02-28' });
  });
  it('periodo anterior tiene la misma duración', () => {
    expect(compareRangeFor('previous_period', sep)).toEqual({ start: '2026-08-08', end: '2026-08-31' });
  });
  it('mes anterior', () => {
    expect(compareRangeFor('previous_month', sep)).toEqual({ start: '2026-08-01', end: '2026-08-24' });
    expect(compareRangeFor('previous_month', { start: '2026-03-01', end: '2026-03-31' })).toEqual({ start: '2026-02-01', end: '2026-02-28' });
    expect(compareRangeFor('previous_month', { start: '2026-03-10', end: '2026-03-31' })).toEqual({ start: '2026-02-10', end: '2026-02-28' });
  });
});
