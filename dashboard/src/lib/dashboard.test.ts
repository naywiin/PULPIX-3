import { describe, expect, it } from 'vitest';
import type { RawDashboard } from '../api/backend';
import { getClient } from '../config/clients';
import { buildMockDashboard } from '../services/mockDashboard';
import { alignDaily, clientConfigFromApi, combinedTotals, leadPlatformView, normalizeDashboard } from './dashboard';
import { compareRangeFor } from './dates';

const client = {
  id: 'blumia', name: 'Blumia', slug: 'blumia', logo: null, active: true,
  google_ads_customer_id: '6364343858', google_ads_login_customer_id: null,
  meta_ad_account_id: '1066569498344996', ga4_property_id: '496398757', ga4_property_ids: ['496398757'], ga4_account_id: '123456',
  google_ads_sources: [{ customer_id: '6364343858', login_customer_id: null, campaign_ids: [], allocation: 'exclusive' as const }],
  meta_ads_sources: [{ ad_account_id: '1066569498344996', campaign_ids: [], allocation: 'exclusive' as const }],
  currency: 'MXN', timezone: 'America/Mexico_City', brand_color: '#0f766e', notes: null,
  hidden_metrics: [],
};

const raw = (over: Partial<RawDashboard['platforms']> = {}, metaCurrency = 'MXN'): RawDashboard => ({
  client,
  source: 'real',
  range: { start: '2026-09-01', end: '2026-09-24' },
  compare_range: { start: '2025-09-01', end: '2025-09-24' },
  generated_at: '2026-09-24T20:00:00+00:00',
  platforms: {
    google_ads: {
      platform: 'google_ads', label: 'Google Ads', status: 'ok', source: 'real', account_id: '6364343858', currency: 'MXN',
      current: {
        totals: { impressions: 4983, clicks: 762, spend: 3613.39, conversions: 19, leads: 4, conversion_value: 8800 },
        campaigns: [{ campaign_id: '1', campaign_name: 'SEARCH', impressions: 1025, clicks: 209, spend: 1269.7, conversions: 4, conversion_value: 1740 }],
        daily: [{ date: '2026-09-01', spend: 100 }, { date: '2026-09-03', spend: 50 }],
      },
      previous: { totals: { spend: 3000, conversions: 10, conversion_value: 6000 }, campaigns: [], daily: [] },
    },
    meta_ads: {
      platform: 'meta_ads', label: 'Meta Ads', status: 'ok', source: 'real', account_id: '1066569498344996', currency: metaCurrency,
      current: {
        totals: { spend: 10028.83, conversions: 16, leads: 16, purchases: 6, conversion_value: 7300, reach: 50000, impressions: 100000, frequency: 2 },
        campaigns: [], adsets: [{ campaign_id: '9', campaign_name: 'C', ad_set_id: '99', ad_set_name: 'AS', spend: 10, objective: 'SALES' }], daily: [],
      },
      previous: { totals: { spend: 9000, conversions: 12, conversion_value: 6000 }, campaigns: [], daily: [] },
    },
    ga4: { platform: 'ga4', label: 'Google Analytics', status: 'error', source: 'real', account_id: '496398757', message: 'Google Analytics temporalmente no disponible', detail: 'boom' },
    ...over,
  },
});

describe('normalizeDashboard', () => {
  it('convierte el payload real y respeta estados por plataforma', () => {
    const d = normalizeDashboard(raw());
    expect(d.source).toBe('real');
    expect(d.clientId).toBe('blumia');
    expect(d.google_ads.status).toBe('ok');
    expect(d.google_ads.current!.totals.roas).toBeCloseTo(2.435, 3);
    expect(d.google_ads.current!.campaigns[0]).toMatchObject({ client_id: 'blumia', platform: 'google_ads', account_id: '6364343858', campaign_name: 'SEARCH' });
    expect(d.meta_ads.current!.adSets[0]).toMatchObject({ ad_set_id: '99', objective: 'SALES' });
    expect(d.meta_ads.current!.totals.frequency).toBe(2);
    expect(d.ga4.status).toBe('error');
    expect(d.ga4.current).toBeNull();
    expect(d.ga4.message).toBe('Google Analytics temporalmente no disponible');
  });

  it('normaliza Spotify con trazabilidad y niveles campaign/ad set/ad', () => {
    const spotify = {
      platform: 'spotify_ads', label: 'Spotify Ads', status: 'ok' as const, source: 'real' as const,
      account_id: '79b62668-1427-4b29-8b73-f4cba3525d0d', currency: 'MXN',
      current: {
        totals: { impressions: 1000, clicks: 10, spend: 200, reach: 800, frequency: 1.25 },
        campaigns: [{ campaign_id: 'c1', campaign_name: 'Santuario', impressions: 1000, clicks: 10, spend: 200, reach: 800,
          business_id: 'e27cd146-ddc1-4551-9757-25fe6bc04016', ad_account_id: '79b62668-1427-4b29-8b73-f4cba3525d0d', video_views: 500, completes: 300 }],
        adsets: [{ campaign_id: 'c1', campaign_name: 'Santuario', ad_set_id: 's1', ad_set_name: 'Set', spend: 200 }],
        ads: [{ campaign_id: 'c1', campaign_name: 'Santuario', ad_set_id: 's1', ad_set_name: 'Set', ad_id: 'a1', ad_name: 'Audio', spend: 200 }],
        daily: [{ date: '2026-09-01', spend: 200 }],
      },
      previous: null,
    };
    const block = normalizeDashboard(raw({ spotify_ads: spotify })).spotify_ads;
    expect(block.status).toBe('ok');
    expect(block.current!.campaigns[0]).toMatchObject({
      platform: 'spotify_ads', business_id: 'e27cd146-ddc1-4551-9757-25fe6bc04016', video_views: 500, completes: 300,
    });
    expect(block.current!.adSets[0]).toMatchObject({ ad_set_id: 's1' });
    expect(block.current!.ads![0]).toMatchObject({ ad_id: 'a1', ad_name: 'Audio' });
  });

  it('conserva resumen Pinterest cuando el backend marca la serie como parcial', () => {
    const pinterest = {
      platform: 'pinterest_ads', label: 'Pinterest Ads', status: 'partial' as const, source: 'real' as const,
      account_id: '549763101458', currency: 'MXN', message: 'Resumen disponible',
      warnings: [{ account_id: '549763101458', status: 'daily_series_partial', message: 'Serie diaria incompleta.' }],
      current: { totals: { spend: 100, impressions: 2000, clicks: 100, checkouts: 5,
        cost_per_checkout: 20, conversion_value: 500, roas: 5, ctr: .05, cpc: 1, currency: 'MXN' },
        campaigns: [], daily: [], sources_count: 1 },
      previous: null,
    };
    const block = normalizeDashboard(raw({ pinterest_ads: pinterest })).pinterest_ads!;
    expect(block.status).toBe('partial');
    expect(block.current!.pinterestTotals).toMatchObject({ spend: 100, checkouts: 5, conversion_value: 500, roas: 5 });
    expect(block.warnings?.[0].message).toBe('Serie diaria incompleta.');
  });
});

describe('combinedTotals', () => {
  it('suma Google + Meta y calcula Blended ROAS = valor / gasto', () => {
    const c = combinedTotals(normalizeDashboard(raw()));
    expect(c.included).toEqual(['google_ads', 'meta_ads']);
    expect(c.current!.spend).toBe(13642.22);
    expect(c.current!.conversions).toBe(25);
    expect(c.current!.cpa).toBeCloseTo(13642.22 / 25, 6);
    // Meta clicks are absent in this fixture, so a combined CPC cannot claim
    // a complete denominator by treating missing clicks as a measured zero.
    expect(c.current!.cpc).toBeNull();
    expect(c.current!.conversion_value).toBe(16100);
    expect(c.current!.value_sources).toEqual(['google_ads', 'meta_ads']);
    expect(c.current!.value_partial).toBe(false);
    expect(c.current!.roas).toBeCloseTo(16100 / (3613.39 + 10028.83), 6);
    expect(c.previous!.spend).toBe(12000);
  });

  it('incluye Spotify como paid media cuando comparte moneda', () => {
    const spotify = {
      platform: 'spotify_ads', label: 'Spotify Ads', status: 'ok' as const, source: 'real' as const,
      account_id: '79b62668-1427-4b29-8b73-f4cba3525d0d', currency: 'MXN',
      current: { totals: { spend: 100, impressions: 1000, clicks: 10 }, campaigns: [], daily: [] },
      previous: { totals: { spend: 50 }, campaigns: [], daily: [] },
    };
    const c = combinedTotals(normalizeDashboard(raw({ spotify_ads: spotify })));
    expect(c.included).toEqual(['google_ads', 'meta_ads', 'spotify_ads']);
    expect(c.current!.spend).toBe(13742.22);
    expect(c.previous!.spend).toBe(12050);
  });

  it('si una plataforma falla, combina solo las disponibles', () => {
    const r = raw({ meta_ads: { platform: 'meta_ads', label: 'Meta Ads', status: 'not_configured', source: 'real', account_id: '1' } });
    const c = combinedTotals(normalizeDashboard(r));
    expect(c.included).toEqual(['google_ads']);
    expect(c.current!.spend).toBe(3613.39);
  });

  it('no mezcla monedas distintas', () => {
    const c = combinedTotals(normalizeDashboard(raw({}, 'USD')));
    expect(c.current!.conversions).toBe(25);
    expect(c.current!.spend).toBeNull();
    expect(c.current!.cpa).toBeNull();
    expect(c.currency).toBeNull();
  });

  it('suma el resultado Meta del modo de cliente y nunca suma conversions y purchases juntos', () => {
    const cirqueRaw = raw();
    cirqueRaw.client = { ...cirqueRaw.client, id: 'cirque-du-soleil-joya' };
    (cirqueRaw.platforms.meta_ads.current!.totals as Record<string, unknown>).purchases = 7;
    const c = combinedTotals(normalizeDashboard(cirqueRaw));
    expect(c.current!.conversions).toBe(26);
  });

  it('Ssiento usa Google conversions y Meta purchases incluso si leads no existe', () => {
    const ssientoRaw = raw();
    ssientoRaw.client = { ...ssientoRaw.client, id: 'ssiento' };
    const metaTotals = ssientoRaw.platforms.meta_ads.current!.totals as Record<string, unknown>;
    metaTotals.spend = 2000;
    metaTotals.conversions = 99;
    metaTotals.purchases = 28;
    metaTotals.leads = null;
    metaTotals.conversion_value = 30000;
    const googleTotals = ssientoRaw.platforms.google_ads.current!.totals as Record<string, unknown>;
    googleTotals.spend = 1000;
    googleTotals.conversions = 10;
    googleTotals.conversion_value = 100000;
    const c = combinedTotals(normalizeDashboard(ssientoRaw));
    expect(c.current!.spend).toBe(3000);
    expect(c.current!.conversions).toBe(38);
    expect(c.current!.cpa).toBeCloseTo(3000 / 38, 6);
    expect(c.current!.cpa_partial).toBe(false);
    expect(c.current!.conversion_value).toBe(130000);
    expect(c.current!.value_partial).toBe(false);
    expect(c.current!.roas).toBeCloseTo(130000 / 3000, 6);
    expect(c.current!.roas_sources).toEqual(['google_ads', 'meta_ads']);
  });

  it('Ssiento suma compras Meta y conversiones Google sin sumar Meta conversions genéricas', () => {
    const ssientoRaw = raw();
    ssientoRaw.client = { ...ssientoRaw.client, id: 'ssiento' };
    const metaTotals = ssientoRaw.platforms.meta_ads.current!.totals as Record<string, unknown>;
    metaTotals.leads = null;
    metaTotals.purchases = 28;
    metaTotals.conversion_value = 61445;
    const c = combinedTotals(normalizeDashboard(ssientoRaw));
    expect(c.current!.spend).toBe(13642.22);
    expect(c.current!.conversions).toBe(47);
    expect(c.current!.conversions_partial).toBe(false);
    expect(c.current!.cpa).toBeCloseTo(13642.22 / 47, 6);
    expect(c.current!.cpa_sources).toEqual(['google_ads', 'meta_ads']);
    expect(c.current!.cpa_partial).toBe(false);
    expect(c.current!.conversion_value).toBe(70245);
    expect(c.current!.value_sources).toEqual(['google_ads', 'meta_ads']);
    expect(c.current!.roas).toBeCloseTo(70245 / 13642.22, 6);
  });

  it('Ssiento mantiene total parcial si Purchase Meta es null, aunque existan leads', () => {
    const ssientoRaw = raw();
    ssientoRaw.client = { ...ssientoRaw.client, id: 'ssiento' };
    const metaTotals = ssientoRaw.platforms.meta_ads.current!.totals as Record<string, unknown>;
    metaTotals.leads = 20;
    metaTotals.purchases = null;
    const c = combinedTotals(normalizeDashboard(ssientoRaw));
    expect(c.current!.conversions).toBe(19);
    expect(c.current!.conversions_partial).toBe(true);
    expect(c.current!.cpa_sources).toEqual(['google_ads']);
    expect(c.current!.conversion_value).toBe(8800);
  });
});

describe('Overview lead platform selection', () => {
  it('keeps totals and daily series on the selected Google/Meta sources', () => {
    const fixture = raw();
    (fixture.platforms.google_ads.current!.totals as Record<string, unknown>).leads = 3;
    (fixture.platforms.google_ads.current!.daily as Record<string, unknown>[])[0].leads = 2;
    (fixture.platforms.meta_ads.current!.totals as Record<string, unknown>).leads = 5;
    fixture.platforms.meta_ads.current!.daily = [{ date: '2026-09-01', leads: 4 }];
    const data = normalizeDashboard(fixture);
    const both = leadPlatformView(data, 'both', ['2026-09-01', '2026-09-02']);
    expect(both.map((item) => [item.key, item.total, item.daily])).toEqual([
      ['google_ads', 3, [2, 0]], ['meta_ads', 5, [4, 0]],
    ]);
    expect(leadPlatformView(data, 'google_ads', ['2026-09-01'])).toMatchObject([
      { key: 'google_ads', total: 3, daily: [2] },
    ]);
    expect(leadPlatformView(data, 'meta_ads', ['2026-09-01'])).toMatchObject([
      { key: 'meta_ads', total: 5, daily: [4] },
    ]);
  });

  it('keeps a measured zero distinct from an unavailable lead breakdown', () => {
    const fixture = raw();
    (fixture.platforms.google_ads.current!.totals as Record<string, unknown>).leads = 0;
    const data = normalizeDashboard(fixture);
    expect(leadPlatformView(data, 'google_ads', ['2026-09-01'])[0].total).toBe(0);
    expect(leadPlatformView(data, 'meta_ads', ['2026-09-01'])[0].total).toBe(16);
  });
});

describe('utilidades', () => {
  it('alinea series diarias rellenando días sin datos con 0', () => {
    expect(alignDaily(['2026-09-01', '2026-09-02', '2026-09-03'], [{ date: '2026-09-01', spend: 100, clicks: 0, impressions: 0, conversions: 0, conversion_value: 0 }, { date: '2026-09-03', spend: 50, clicks: 0, impressions: 0, conversions: 0, conversion_value: 0 }], 'spend')).toEqual([100, 0, 50]);
    expect(alignDaily(['2026-09-01'], undefined, 'spend')).toEqual([null]);
    expect(alignDaily(['2026-09-01'], [{ date: '2026-09-01', spend: 0, conversions: 0, conversion_value: 0, clicks: 0, impressions: 0, leads: null }], 'leads')).toEqual([null]);
  });

  it('adapta el cliente del registro sin exponer nada más que IDs', () => {
    const c = clientConfigFromApi({ ...client, ga4_property_id: null, ga4_property_ids: [] });
    expect(c.sources.google_ads).toEqual({ accountId: '6364343858', mode: 'api' });
    expect(c.sources.google_analytics).toBeUndefined();
  });

  it('propaga hidden_metrics del registro al ClientConfig', () => {
    expect(clientConfigFromApi({ ...client, hidden_metrics: ['roas'] }).hiddenMetrics).toEqual(['roas']);
    expect(clientConfigFromApi({ ...client, hidden_metrics: [] }).hiddenMetrics).toEqual([]);
  });
});

describe('buildMockDashboard', () => {
  it('marca todo como MOCK y conserva los valores de referencia de Blumia', async () => {
    const range = { start: '2026-09-01', end: '2026-09-24' };
    const sel = { preset: 'custom' as const, range, compare: 'previous_year' as const, compareRange: compareRangeFor('previous_year', range) };
    const d = await buildMockDashboard(getClient('blumia'), sel, '2026-09-24');
    expect(d.source).toBe('mock');
    for (const b of [d.google_ads, d.meta_ads, d.spotify_ads, d.ga4]) expect(b.source).toBe('mock');
    expect(d.spotify_ads.status).toBe('not_linked');
    expect(d.google_ads.current!.totals.spend).toBe(3613.39);
    expect(Math.round(d.google_ads.current!.totals.roas * 100) / 100).toBe(2.44);
    expect(d.google_ads.current!.daily).toHaveLength(24);
    const dailySum = d.google_ads.current!.daily.reduce((a, p) => a + p.spend, 0);
    expect(Math.round(dailySum * 100) / 100).toBe(3613.39);
    expect(d.meta_ads.current!.totals.spend).toBe(10028.83);
    expect(d.google_ads.previous!.totals.spend).toBeGreaterThan(0);
    expect(d.ga4.current!.totals.sessions).toBeGreaterThan(0);
  }, 20_000);

  it('cliente sin fuentes → plataformas no asignadas', async () => {
    const range = { start: '2026-09-01', end: '2026-09-24' };
    const d = await buildMockDashboard(getClient('cliente-sin-conectar'), { preset: 'custom', range, compare: 'none', compareRange: null }, '2026-09-24');
    expect(d.google_ads.status).toBe('not_linked');
    expect(d.ga4.status).toBe('not_linked');
  });
});

/* ------------------------------------------------- GA4 multi-propiedad */

const ga4Metrics = (sessions: number, engaged: number, users: number, key_events = 0, revenue = 0) => ({
  users,
  sessions,
  engaged_sessions: engaged,
  engagement_rate: sessions ? engaged / sessions : 0,
  key_events,
  revenue,
});

const ga4Prop = (property_id: string, property_name: string, sessions: number, engaged: number, users: number, currency = 'MXN') => ({
  property_id,
  property_name,
  currency,
  status: 'ok',
  current: {
    property_id,
    property_name,
    currency,
    totals: ga4Metrics(sessions, engaged, users, 3, 900),
    daily: [{ date: '2026-09-01', ...ga4Metrics(sessions, engaged, users) }],
    by_source_medium: [{ name: 'google / cpc', ...ga4Metrics(sessions, engaged, users) }],
    by_campaign: [],
    by_landing_page: [],
  },
  previous: {
    totals: ga4Metrics(sessions / 2, engaged / 2, users / 2),
    daily: [],
    by_source_medium: [],
    by_campaign: [],
    by_landing_page: [],
  },
});

/** Bloque GA4 con dos propiedades sanas y una caída, tal como lo manda el backend. */
const ga4MultiBlock = () => ({
  platform: 'ga4',
  label: 'Google Analytics',
  status: 'ok' as const,
  source: 'real' as const,
  account_id: '264078195',
  property_ids: ['264078195', '304259869', '448212614'],
  currency: 'MXN',
  current: {
    totals: ga4Metrics(700, 280, 120, 6, 1800),
    daily: [{ date: '2026-09-01', ...ga4Metrics(700, 280, 120) }],
    by_source_medium: [{ name: 'google / cpc', ...ga4Metrics(700, 280, 120) }],
    by_campaign: [],
    by_landing_page: [],
    properties_count: 2,
  },
  previous: { totals: ga4Metrics(350, 140, 60), daily: [], by_source_medium: [], by_campaign: [], by_landing_page: [] },
  properties: [
    ga4Prop('264078195', 'Estados Unidos (Miami)', 300, 120, 80, 'USD'),
    ga4Prop('304259869', 'México', 400, 160, 40),
    { property_id: '448212614', status: 'error', message: 'Propiedad 448212614 no disponible', detail: 'cuota agotada' },
  ],
  warnings: [
    { property_id: '448212614', status: 'error', message: 'Propiedad 448212614 no disponible' },
    { property_id: null, status: 'mixed_currency', message: 'Las propiedades usan monedas distintas (USD, MXN): Revenue es una suma sin convertir.' },
  ],
  mixed_currency: true,
});

describe('GA4 con varias propiedades', () => {
  const block = () => normalizeDashboard(raw({ ga4: ga4MultiBlock() as never })).ga4;

  it('expone todas las propiedades seleccionadas, no solo la principal', () => {
    const b = block();
    expect(b.status).toBe('ok');
    expect(b.propertyIds).toEqual(['264078195', '304259869', '448212614']);
    expect(b.accountId).toBe('264078195'); // compatibilidad con la vista anterior
  });

  it('normaliza el consolidado que envía el backend', () => {
    const cur = block().current!;
    expect(cur.totals.sessions).toBe(700);
    expect(cur.totals.engaged_sessions).toBe(280);
    expect(cur.totals.engagement_rate).toBeCloseTo(280 / 700, 6);
    expect(cur.totals.users).toBe(120); // suma sin deduplicar
    expect(cur.propertiesCount).toBe(2);
    expect(cur.by_source_medium).toEqual([expect.objectContaining({ name: 'google / cpc', sessions: 700 })]);
  });

  it('conserva la comparación del consolidado', () => {
    expect(block().previous!.totals.sessions).toBe(350);
  });

  it('deja cada propiedad disponible por separado, con su moneda y su comparación', () => {
    const props = block().properties!;
    expect(props.map((p) => p.propertyId)).toEqual(['264078195', '304259869', '448212614']);
    expect(props[0]).toMatchObject({ propertyName: 'Estados Unidos (Miami)', currency: 'USD', status: 'ok' });
    expect(props[0].current!.totals.sessions).toBe(300);
    expect(props[0].previous!.totals.sessions).toBe(150);
    expect(props[1]).toMatchObject({ propertyName: 'México', currency: 'MXN' });
    expect(props[1].current!.totals.sessions).toBe(400);
  });

  it('la propiedad que falló no rompe el bloque y llega como aviso con su ID', () => {
    const b = block();
    const broken = b.properties!.find((p) => p.propertyId === '448212614')!;
    expect(broken.status).toBe('error');
    expect(broken.current).toBeNull();
    expect(b.warnings!.map((w) => w.propertyId)).toEqual(['448212614', null]);
    expect(b.warnings![0].message).toContain('448212614');
    expect(b.mixedCurrency).toBe(true);
  });

  it('una sola propiedad sigue funcionando como antes', () => {
    const single = {
      ...ga4MultiBlock(),
      property_ids: ['264078195'],
      properties: [ga4Prop('264078195', 'Estados Unidos (Miami)', 300, 120, 80)],
      warnings: [],
      mixed_currency: false,
    };
    const b = normalizeDashboard(raw({ ga4: single as never })).ga4;
    expect(b.propertyIds).toEqual(['264078195']);
    expect(b.warnings).toEqual([]);
    expect(b.current!.totals.sessions).toBe(700);
  });

  it('un backend sin property_ids cae al account_id suelto', () => {
    const legacy = { ...ga4MultiBlock(), property_ids: undefined, properties: undefined, warnings: undefined };
    const b = normalizeDashboard(raw({ ga4: legacy as never })).ga4;
    expect(b.propertyIds).toEqual(['264078195']);
    expect(b.properties).toEqual([]);
    expect(b.current!.totals.sessions).toBe(700);
  });
});

/* ------------------------------------------- Google Ads / Meta multi-cuenta */

const adsMultiBlock = () => ({
  platform: 'google_ads',
  label: 'Google Ads',
  status: 'ok' as const,
  source: 'real' as const,
  account_id: '7000000002',
  currency: 'MXN',
  current: {
    totals: { impressions: 4500, clicks: 250, spend: 900, conversions: 24, conversion_value: 4600 },
    campaigns: [
      { campaign_id: '301', campaign_name: 'Destino Brand', impressions: 500, clicks: 50, spend: 100,
        conversions: 4, conversion_value: 600, source_account_id: '7000000002', source_account_name: 'Cuenta propia' },
      { campaign_id: '201', campaign_name: 'Destino Search', impressions: 4000, clicks: 200, spend: 800,
        conversions: 20, conversion_value: 4000, source_account_id: '7000000001', source_account_name: 'Cuenta Origen' },
    ],
    daily: [{ date: '2026-09-01', spend: 450, clicks: 125, impressions: 2250, conversions: 12, conversion_value: 2300 }],
    sources_count: 2,
    reach_is_sum: true,
  },
  previous: { totals: { spend: 450, conversions: 12, conversion_value: 2300 }, campaigns: [], daily: [] },
  sources: [
    { account_id: '7000000002', account_name: 'Cuenta propia', campaign_ids: [], allocation: 'exclusive',
      status: 'ok', currency: 'MXN' },
    { account_id: '7000000001', account_name: 'Cuenta Origen', campaign_ids: ['201'], allocation: 'exclusive',
      status: 'ok', currency: 'MXN' },
    { account_id: '7000000003', account_name: null, campaign_ids: [], allocation: 'exclusive',
      status: 'error', message: 'La cuenta 7000000003 no pudo consultarse', detail: 'cuota agotada', currency: null },
  ],
  warnings: [
    { account_id: null, status: 'partial', message: '2 de 3 fuentes de Google Ads cargadas.' },
    { account_id: '7000000003', status: 'error', message: 'La cuenta 7000000003 no pudo consultarse', detail: 'cuota agotada' },
  ],
});

describe('Google Ads con campañas de varias cuentas', () => {
  const blk = () => normalizeDashboard(raw({ google_ads: adsMultiBlock() as never })).google_ads;

  it('cada campaña conserva la cuenta de la que viene', () => {
    const rows = blk().current!.campaigns;
    expect(rows.map((r) => [r.campaign_name, r.source_account_id, r.source_account_name])).toEqual([
      ['Destino Brand', '7000000002', 'Cuenta propia'],
      ['Destino Search', '7000000001', 'Cuenta Origen'],
    ]);
  });

  it('expone cuántas cuentas aportaron y si el reach viene sumado', () => {
    expect(blk().current!.sourcesCount).toBe(2);
    expect(blk().current!.reachIsSum).toBe(true);
  });

  it('las métricas derivadas se recalculan desde los totales, no se promedian', () => {
    const t = blk().current!.totals;
    expect(t.ctr).toBeCloseTo(250 / 4500, 9);
    expect(t.cpc).toBeCloseTo(900 / 250, 9);
    expect(t.roas).toBeCloseTo(4600 / 900, 9);
    // El promedio de los CTR de las dos cuentas sería otro número.
    expect(t.ctr).not.toBeCloseTo((50 / 500 + 200 / 4000) / 2, 6);
  });

  it('normaliza el estado de cada cuenta de origen', () => {
    const sources = blk().sources!;
    expect(sources.map((s) => [s.accountId, s.status])).toEqual([
      ['7000000002', 'ok'],
      ['7000000001', 'ok'],
      ['7000000003', 'error'],
    ]);
    expect(sources[1].campaignIds).toEqual(['201']);
    expect(sources[1].allocation).toBe('exclusive');
  });

  it('una cuenta caída llega como aviso sin tumbar el bloque', () => {
    const b = blk();
    expect(b.status).toBe('ok');
    expect(b.current!.totals.spend).toBe(900);
    expect(b.warnings!.map((w) => w.status)).toEqual(['partial', 'error']);
    expect(b.warnings![0].message).toContain('2 de 3');
    expect(b.warnings![1].accountId).toBe('7000000003');
    expect(b.warnings![1].detail).toBe('cuota agotada');
  });

  it('un bloque de una sola cuenta sigue funcionando igual que antes', () => {
    const single = { ...adsMultiBlock(), sources: undefined, warnings: undefined };
    delete (single.current as Record<string, unknown>).sources_count;
    const b = normalizeDashboard(raw({ google_ads: single as never })).google_ads;
    expect(b.sources).toEqual([]);
    expect(b.warnings).toEqual([]);
    expect(b.current!.sourcesCount).toBeUndefined();
    expect(b.current!.campaigns).toHaveLength(2);
  });

  it('sin procedencia explícita, la campaña se atribuye a la cuenta del bloque', () => {
    const legacy = adsMultiBlock();
    legacy.current.campaigns = legacy.current.campaigns.map(({ source_account_id: _a, source_account_name: _b, ...r }) => r) as never;
    const rows = normalizeDashboard(raw({ google_ads: legacy as never })).google_ads.current!.campaigns;
    expect(rows.every((r) => r.source_account_id === '7000000002')).toBe(true);
  });
});
