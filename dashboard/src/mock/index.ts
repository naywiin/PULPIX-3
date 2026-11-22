import { deriveMetrics, round2 } from '../lib/metrics';
import type { EntityLevel, MetricRecord, Platform } from '../types/metrics';
import type { DateRange } from '../types/report';
import { BLUMIA_MOCK } from './blumia';
import { DEMO_MOCK } from './demo';
import type { MockAccount, MockClientData } from './datasets';
import { aggregateEntity, type EntityAggregate, type MockEntity } from './series';

const MOCK_DATA: Record<string, MockClientData> = {
  blumia: BLUMIA_MOCK,
  'cliente-demo': DEMO_MOCK,
};

export function getMockAccount(clientId: string, platform: Platform): MockAccount | undefined {
  return MOCK_DATA[clientId]?.[platform];
}

/** Controla la latencia y fallos simulados. Añade ?mock=error o ?mock=slow a la URL para probar estados. */
export async function mockLatency(): Promise<void> {
  const flag = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('mock') : null;
  await new Promise((r) => setTimeout(r, flag === 'slow' ? 3000 : 350 + Math.random() * 300));
  if (flag === 'error') throw new Error('Error simulado de la fuente de datos (mock=error).');
}

function toRecord(
  clientId: string,
  platform: Platform,
  account: MockAccount,
  base: EntityAggregate,
  range: DateRange,
  extra: Partial<MetricRecord> & Pick<MetricRecord, 'campaign_id' | 'campaign_name'>,
): MetricRecord {
  return {
    client_id: clientId,
    platform,
    account_id: account.accountId,
    date: range.start,
    date_end: range.end,
    ...deriveMetrics(base),
    ...extra,
  };
}

/** Consulta mock agregada por campaña o ad set, ya normalizada al modelo común. */
export function queryMock(
  clientId: string,
  platform: Platform,
  range: DateRange,
  level: EntityLevel,
  today: string,
): MetricRecord[] {
  const account = getMockAccount(clientId, platform);
  if (!account) return [];
  const out: MetricRecord[] = [];

  for (const c of account.campaigns) {
    const adSetAggs = c.adSets.map((a) => {
      const entity: MockEntity = {
        key: `${clientId}|${platform}|${a.id}`,
        anchor: account.anchor,
        totals: a.totals,
        activeFrom: c.activeFrom ?? account.dataStart,
      };
      return { adSet: a, agg: aggregateEntity(entity, range, today) };
    });

    if (level === 'ad_set') {
      for (const { adSet, agg } of adSetAggs) {
        if (!agg.impressions && !agg.clicks) continue;
        out.push(
          toRecord(clientId, platform, account, agg, range, {
            campaign_id: c.id,
            campaign_name: c.name,
            ad_set_id: adSet.id,
            ad_set_name: adSet.name,
            objective: c.objective,
          }),
        );
      }
      continue;
    }

    const sum: EntityAggregate = { impressions: 0, clicks: 0, spend: 0, conversions: 0, conversion_value: 0, inline_link_clicks: 0, reach: 0 };
    for (const { agg } of adSetAggs) {
      sum.impressions += agg.impressions;
      sum.clicks += agg.clicks;
      sum.spend += agg.spend;
      sum.conversions += agg.conversions;
      sum.conversion_value += agg.conversion_value;
      sum.inline_link_clicks += agg.inline_link_clicks;
      sum.reach += agg.reach;
    }
    if (!sum.impressions && !sum.clicks) continue;
    sum.spend = round2(sum.spend);
    sum.conversion_value = round2(sum.conversion_value);
    // Solapamiento de audiencia entre ad sets de una misma campaña
    if (adSetAggs.length > 1) sum.reach = Math.round(sum.reach * 0.92);
    out.push(toRecord(clientId, platform, account, sum, range, { campaign_id: c.id, campaign_name: c.name, objective: c.objective }));
  }
  return out;
}
