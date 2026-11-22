import { describe, expect, it } from 'vitest';
import type { PerformanceDetailRow } from '../types/reporting';
import { detailStatus, filterDetailRows } from './performanceDetailFilters';

const row = (id: string, platform: 'google_ads' | 'meta_ads', status: string | null, spend: number | null): PerformanceDetailRow => ({
  level: 'campaign', platform, source_account_id: 'acct', campaign_id: id, campaign_name: id,
  status, effective_status: platform === 'meta_ads' ? status : null, status_label: status ?? 'Desconocido',
  spend, impressions: 0, clicks: 0, conversions: null, leads: null, conversion_value: null,
  cpc: null, ctr: null, cpa: null, cpl: null, roas: null,
});

const fixtures = [
  row('active-spend', 'google_ads', 'ENABLED', 15),
  row('active-zero', 'google_ads', 'ENABLED', 0),
  row('paused-spend', 'google_ads', 'PAUSED', 20),
  row('paused-zero', 'google_ads', 'REMOVED', 0),
  row('unknown-spend', 'google_ads', 'MYSTERY', 3),
  row('missing-spend', 'meta_ads', 'ACTIVE', null),
  row('missing-status', 'meta_ads', null, 7),
];

describe('filtros independientes de campañas', () => {
  it('combina Activos + Con gasto sin incluir otros estados ni gasto ausente', () => {
    expect(filterDetailRows(fixtures, 'active', 'with_spend').map((item) => item.campaign_id)).toEqual(['active-spend']);
  });
  it('combina Inactivos + Con gasto y Todos + Con gasto independientemente', () => {
    expect(filterDetailRows(fixtures, 'inactive', 'with_spend').map((item) => item.campaign_id)).toEqual(['paused-spend']);
    expect(filterDetailRows(fixtures, 'all', 'with_spend').map((item) => item.campaign_id)).toEqual(['active-spend', 'paused-spend', 'unknown-spend', 'missing-status']);
  });
  it('UNKNOWN incluye estados ausentes/no reconocidos, nunca los marca inactivos', () => {
    expect(detailStatus(fixtures[4])).toBe('unknown');
    expect(detailStatus(fixtures[5])).toBe('active');
    expect(detailStatus(fixtures[6])).toBe('unknown');
    expect(filterDetailRows(fixtures, 'unknown', 'all').map((item) => item.campaign_id)).toEqual(['unknown-spend', 'missing-status']);
  });
  it('usa effective_status de Meta y clasifica WITH_ISSUES como inactivo', () => {
    const metaActive = { ...row('meta-active', 'meta_ads', 'PAUSED', 4), effective_status: 'ACTIVE' };
    const metaIssues = row('meta-issues', 'meta_ads', 'WITH_ISSUES', 4);
    expect(detailStatus(metaActive)).toBe('active');
    expect(detailStatus(metaIssues)).toBe('inactive');
    expect(filterDetailRows([metaActive, metaIssues], 'inactive', 'with_spend')).toEqual([metaIssues]);
  });
  it('gasto null no es cero y queda fuera de ambos filtros específicos', () => {
    expect(filterDetailRows(fixtures, 'all', 'without_spend').map((item) => item.campaign_id)).toEqual(['active-zero', 'paused-zero']);
    expect(filterDetailRows(fixtures, 'all', 'with_spend')).not.toContain(fixtures[5]);
  });
});
