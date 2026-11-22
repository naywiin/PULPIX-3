import type { MockAccount, MockClientData } from './datasets';

const ANCHOR = { start: '2026-09-01', end: '2026-09-24' };

const googleAds: MockAccount = {
  accountId: 'mock-gads-demo',
  anchor: ANCHOR,
  dataStart: '2025-01-01',
  campaigns: [
    {
      id: 'demo-gads-search-brand',
      name: 'SEARCH | BRAND | MÉXICO | DEMO',
      objective: 'SALES',
      adSets: [
        { id: 'demo-gads-search-brand', name: 'SEARCH | BRAND', totals: { impressions: 18420, clicks: 2947, spend: 18750.4, conversions: 138, conversion_value: 126840 } },
      ],
    },
    {
      id: 'demo-gads-pmax-sales',
      name: 'PMAX | VENTAS | NACIONAL | DEMO',
      objective: 'SALES',
      adSets: [
        { id: 'demo-gads-pmax-sales', name: 'PMAX | VENTAS', totals: { impressions: 94830, clicks: 5284, spend: 36210.75, conversions: 247, conversion_value: 238900 } },
      ],
    },
    {
      id: 'demo-gads-search-generic',
      name: 'SEARCH | GENÉRICAS | NACIONAL | DEMO',
      objective: 'LEADS',
      adSets: [
        { id: 'demo-gads-search-generic', name: 'SEARCH | GENÉRICAS', totals: { impressions: 32750, clicks: 2410, spend: 22490.2, conversions: 92, conversion_value: 81650 } },
      ],
    },
  ],
};

const metaAds: MockAccount = {
  accountId: 'mock-meta-demo',
  anchor: ANCHOR,
  dataStart: '2025-01-01',
  campaigns: [
    {
      id: 'demo-meta-prospecting',
      name: 'VENTAS | PROSPECTING | NACIONAL | DEMO',
      objective: 'SALES',
      adSets: [
        { id: 'demo-meta-broad', name: 'BROAD | 25–54', totals: { impressions: 384500, clicks: 7210, inline_link_clicks: 6040, reach: 172300, spend: 48620.5, conversions: 126, conversion_value: 128400 } },
        { id: 'demo-meta-lookalike', name: 'LOOKALIKE | COMPRADORES 2%', totals: { impressions: 198300, clicks: 4330, inline_link_clicks: 3710, reach: 89300, spend: 27340.25, conversions: 91, conversion_value: 99450 } },
      ],
    },
    {
      id: 'demo-meta-retargeting',
      name: 'VENTAS | RETARGETING | DEMO',
      objective: 'SALES',
      adSets: [
        { id: 'demo-meta-visitors', name: 'VISITANTES | 30 DÍAS', totals: { impressions: 97400, clicks: 3120, inline_link_clicks: 2780, reach: 28400, spend: 19480.15, conversions: 84, conversion_value: 93420 } },
        { id: 'demo-meta-cart', name: 'CARRITO | 14 DÍAS', totals: { impressions: 52800, clicks: 1940, inline_link_clicks: 1740, reach: 13900, spend: 11890.6, conversions: 62, conversion_value: 72160 } },
      ],
    },
    {
      id: 'demo-meta-awareness',
      name: 'AWARENESS | ALWAYS ON | DEMO',
      objective: 'AWARENESS',
      adSets: [
        { id: 'demo-meta-reach', name: 'ALCANCE | NACIONAL', totals: { impressions: 612700, clicks: 2840, inline_link_clicks: 1190, reach: 348200, spend: 14950, conversions: 0, conversion_value: 0 } },
      ],
    },
  ],
};

const googleAnalytics: MockAccount = {
  accountId: 'mock-ga4-demo',
  anchor: ANCHOR,
  dataStart: '2025-01-01',
  campaigns: [
    { id: 'demo-ga-google', name: 'google / cpc', objective: 'OTHER', adSets: [{ id: 'demo-ga-google', name: 'google / cpc', totals: { impressions: 0, clicks: 10641, reach: 8210, spend: 0, conversions: 342, conversion_value: 318250 } }] },
    { id: 'demo-ga-meta', name: 'facebook / paid', objective: 'OTHER', adSets: [{ id: 'demo-ga-meta', name: 'facebook / paid', totals: { impressions: 0, clicks: 16820, reach: 13240, spend: 0, conversions: 287, conversion_value: 276410 } }] },
    { id: 'demo-ga-organic', name: 'google / organic', objective: 'OTHER', adSets: [{ id: 'demo-ga-organic', name: 'google / organic', totals: { impressions: 0, clicks: 7840, reach: 6390, spend: 0, conversions: 98, conversion_value: 86200 } }] },
  ],
};

export const DEMO_MOCK: MockClientData = {
  google_ads: googleAds,
  meta_ads: metaAds,
  google_analytics: googleAnalytics,
};
