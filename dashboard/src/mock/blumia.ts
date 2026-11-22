import type { MockAccount, MockClientData } from './datasets';

/**
 * Dataset mock de Blumia. Los totales corresponden al periodo 1–24 sep 2026
 * ("Este mes" en la fecha de creación) y reproducen los valores de referencia:
 *
 *   Google Ads: Cost 3,613.39 · Conv 19 · Conv Value 8,800 · CPA 190.18 · ROAS 2.44x
 *   Meta Ads:   Gasto 10,028.83 · conversiones internas 16 · ROAS 0.73x
 */
const ANCHOR = { start: '2026-09-01', end: '2026-09-24' };

const googleAds: MockAccount = {
  accountId: 'mock-gads-blumia',
  anchor: ANCHOR,
  dataStart: '2025-01-01',
  campaigns: [
    {
      id: 'gads-search-ver',
      name: 'SEARCH | CONVERSIÓN | VERACRUZ, VER | BLUMIA | PERFORMANCE',
      objective: 'SALES',
      adSets: [
        { id: 'gads-search-ver', name: 'SEARCH', totals: { impressions: 1025, clicks: 209, spend: 1269.7, conversions: 4, conversion_value: 1740 } },
      ],
    },
    {
      id: 'gads-pmax-ver-ene26',
      name: 'PMAX / CONVERSIÓN / VERACRUZ, VER / BLUMIA / PERFORMANCE / ENERO 2026',
      objective: 'SALES',
      activeFrom: '2026-01-08',
      adSets: [
        { id: 'gads-pmax-ver-ene26', name: 'PMAX', totals: { impressions: 3958, clicks: 553, spend: 2343.69, conversions: 15, conversion_value: 7060 } },
      ],
    },
  ],
};

const metaAds: MockAccount = {
  accountId: 'mock-meta-blumia',
  anchor: ANCHOR,
  dataStart: '2025-01-01',
  campaigns: [
    {
      id: 'meta-conv-catalogo',
      name: 'CONVERSIONES | CATÁLOGO | VERACRUZ | BLUMIA',
      objective: 'SALES',
      adSets: [
        { id: 'meta-as-advplus-broad', name: 'ADV+ | CATÁLOGO | BROAD', totals: { impressions: 118900, clicks: 1980, inline_link_clicks: 1570, reach: 42100, spend: 3288.15, conversions: 7, conversion_value: 3240 } },
        { id: 'meta-as-int-hogar', name: 'INTERESES | HOGAR Y DECORACIÓN', totals: { impressions: 63440, clicks: 1140, inline_link_clicks: 910, reach: 24800, spend: 2124.25, conversions: 3, conversion_value: 1390 } },
      ],
    },
    {
      id: 'meta-conv-retargeting',
      name: 'CONVERSIONES | RETARGETING | BLUMIA',
      objective: 'SALES',
      adSets: [
        { id: 'meta-as-rt-visit30', name: 'RT | VISITANTES 30D', totals: { impressions: 29830, clicks: 702, inline_link_clicks: 566, reach: 5600, spend: 1402.51, conversions: 4, conversion_value: 1875.05 } },
        { id: 'meta-as-rt-cart14', name: 'RT | CARRITO 14D', totals: { impressions: 18380, clicks: 403, inline_link_clicks: 324, reach: 3100, spend: 784.42, conversions: 2, conversion_value: 816 } },
      ],
    },
    {
      id: 'meta-trafico-ver',
      name: 'TRÁFICO | VERACRUZ | BLUMIA',
      objective: 'TRAFFIC',
      adSets: [
        { id: 'meta-as-traf-2545', name: 'TRÁFICO | VERACRUZ 25-45', totals: { impressions: 61200, clicks: 1850, inline_link_clicks: 1560, reach: 24100, spend: 1029.5, conversions: 0, conversion_value: 0 } },
        { id: 'meta-as-traf-bdr', name: 'TRÁFICO | BOCA DEL RÍO', totals: { impressions: 35200, clicks: 1020, inline_link_clicks: 850, reach: 15600, spend: 500, conversions: 0, conversion_value: 0 } },
      ],
    },
    {
      id: 'meta-alcance',
      name: 'RECONOCIMIENTO | ALCANCE | BLUMIA',
      objective: 'AWARENESS',
      adSets: [
        { id: 'meta-as-alc-metro', name: 'ALCANCE | VERACRUZ METRO', totals: { impressions: 142800, clicks: 610, inline_link_clicks: 380, reach: 71300, spend: 900, conversions: 0, conversion_value: 0 } },
      ],
    },
  ],
};

/**
 * GA4 mapeado al modelo común: campaign = source / medium,
 * clicks = sessions, reach = total users, conversions = key events,
 * conversion_value = revenue. (Sin impresiones ni gasto.)
 */
const googleAnalytics: MockAccount = {
  accountId: 'mock-ga4-blumia',
  anchor: ANCHOR,
  dataStart: '2025-01-01',
  campaigns: [
    { id: 'ga-google-cpc', name: 'google / cpc', objective: 'OTHER', adSets: [{ id: 'ga-google-cpc', name: 'google / cpc', totals: { impressions: 0, clicks: 731, reach: 612, spend: 0, conversions: 21, conversion_value: 9120 } }] },
    { id: 'ga-meta-paid', name: 'facebook / paid', objective: 'OTHER', adSets: [{ id: 'ga-meta-paid', name: 'facebook / paid', totals: { impressions: 0, clicks: 6420, reach: 5480, spend: 0, conversions: 14, conversion_value: 6870 } }] },
    { id: 'ga-organic', name: 'google / organic', objective: 'OTHER', adSets: [{ id: 'ga-organic', name: 'google / organic', totals: { impressions: 0, clicks: 2310, reach: 1890, spend: 0, conversions: 9, conversion_value: 4210 } }] },
    { id: 'ga-direct', name: '(direct) / (none)', objective: 'OTHER', adSets: [{ id: 'ga-direct', name: '(direct) / (none)', totals: { impressions: 0, clicks: 1480, reach: 1210, spend: 0, conversions: 6, conversion_value: 2950 } }] },
  ],
};

export const BLUMIA_MOCK: MockClientData = {
  google_ads: googleAds,
  meta_ads: metaAds,
  google_analytics: googleAnalytics,
};
