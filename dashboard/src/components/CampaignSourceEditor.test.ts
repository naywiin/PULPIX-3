/**
 * Lógica del selector de fuentes de campañas.
 *
 * PENDIENTE (segunda etapa, decisión del usuario 2026-09-28): los tests de
 * INTERACCIÓN reales — abrir el selector, cargar campañas de una cuenta,
 * buscar, marcar/desmarcar, alternar exclusiva/compartida, guardar, editar,
 * eliminar y ver el error de conflicto — requieren `jsdom` y
 * `@testing-library/react`, que hoy no son dependencias del proyecto. Se
 * decidió no añadirlas justo antes de un despliegue. Aquí se cubre toda la
 * lógica pura que esos tests ejercitarían; la capa de render queda cubierta por
 * el typecheck y el build.
 */
import { describe, expect, it } from 'vitest';
import { METRICS } from '../config/metrics';
import { adSourcesOf, primaryAccountId, sameSources } from '../pages/SettingsClients';
import type { ApiClient, GoogleAdsSource, MetaAdsSource } from '../types/dashboard';
import { sourceAccountId } from '../types/dashboard';
import { campaignSummary, sourceLevel } from './CampaignSourceEditor';

const gads = (over: Partial<GoogleAdsSource> = {}): GoogleAdsSource => ({
  customer_id: '7000000001',
  login_customer_id: null,
  campaign_ids: [],
  allocation: 'exclusive',
  ...over,
});

const meta = (over: Partial<MetaAdsSource> = {}): MetaAdsSource => ({
  ad_account_id: '8000000001',
  campaign_ids: [],
  allocation: 'exclusive',
  ...over,
});

const client = (over: Partial<ApiClient> = {}): ApiClient =>
  ({
    id: 'destino',
    name: 'Destino',
    slug: 'destino',
    logo: null,
    active: true,
    google_ads_customer_id: null,
    google_ads_login_customer_id: null,
    meta_ad_account_id: null,
    google_ads_sources: [],
    meta_ads_sources: [],
    ga4_property_id: null,
    ga4_property_ids: [],
    ga4_account_id: null,
    currency: 'MXN',
    timezone: 'America/Mexico_City',
    brand_color: null,
    notes: null,
    hidden_metrics: [],
    ...over,
  }) as ApiClient;

describe('resumen de una fuente de campañas', () => {
  it('sin nada marcado es la cuenta completa', () => {
    expect(campaignSummary(gads({ campaign_ids: [] }))).toBe('Cuenta completa');
  });

  it('cuenta las campañas completas seleccionadas', () => {
    expect(campaignSummary(gads({ campaign_ids: ['201'] }))).toBe('1 campaña completa');
    expect(campaignSummary(gads({ campaign_ids: ['201', '202'] }))).toBe('2 campañas completas');
    expect(campaignSummary(meta({ campaign_ids: ['601', '602', '603'] }))).toBe('3 campañas completas');
  });

  it('describe los conjuntos de anuncios y su campaña', () => {
    const src = meta({ adset_selections: [{ campaign_id: '601', adset_ids: ['6011'], allocation: 'exclusive' }] });
    expect(campaignSummary(src)).toBe('1 conjunto de anuncios de 1 campaña');
    const dos = meta({
      adset_selections: [
        { campaign_id: '601', adset_ids: ['6011', '6012'], allocation: 'exclusive' },
        { campaign_id: '602', adset_ids: ['6021'], allocation: 'exclusive' },
      ],
    });
    expect(campaignSummary(dos)).toBe('3 conjuntos de anuncios de 2 campañas');
  });

  it('combina campañas completas y conjuntos sueltos', () => {
    const src = meta({
      campaign_ids: ['602'],
      adset_selections: [{ campaign_id: '601', adset_ids: ['6011'], allocation: 'exclusive' }],
    });
    expect(campaignSummary(src)).toBe('1 campaña completa · 1 conjunto de anuncios de 1 campaña');
  });
});

describe('nivel de una fuente', () => {
  it('distingue cuenta, campaña, ad set y mixto', () => {
    expect(sourceLevel(meta({}))).toBe('account');
    expect(sourceLevel(meta({ campaign_ids: ['601'] }))).toBe('campaign');
    expect(sourceLevel(meta({ adset_selections: [{ campaign_id: '601', adset_ids: ['6011'], allocation: 'exclusive' }] }))).toBe('adset');
    expect(
      sourceLevel(meta({ campaign_ids: ['602'], adset_selections: [{ campaign_id: '601', adset_ids: ['6011'], allocation: 'exclusive' }] })),
    ).toBe('mixed');
  });

  it('Google Ads solo tiene cuenta o campañas', () => {
    expect(sourceLevel(gads({}))).toBe('account');
    expect(sourceLevel(gads({ campaign_ids: ['201'] }))).toBe('campaign');
  });
});

describe('identidad de una fuente', () => {
  it('lee el ID de cuenta de cada plataforma', () => {
    expect(sourceAccountId(gads({ customer_id: '123' }))).toBe('123');
    expect(sourceAccountId(meta({ ad_account_id: '456' }))).toBe('456');
  });
});

describe('fuentes de un cliente', () => {
  it('devuelve las fuentes de cada plataforma por separado', () => {
    const c = client({
      google_ads_sources: [gads({ customer_id: '111' }), gads({ customer_id: '222', campaign_ids: ['201'] })],
      meta_ads_sources: [meta({ ad_account_id: '333' })],
    });
    expect(adSourcesOf(c, 'google_ads').map(sourceAccountId)).toEqual(['111', '222']);
    expect(adSourcesOf(c, 'meta_ads').map(sourceAccountId)).toEqual(['333']);
  });

  it('la primera fuente es la cuenta principal', () => {
    const c = client({ google_ads_sources: [gads({ customer_id: '111' }), gads({ customer_id: '222' })] });
    expect(sourceAccountId(adSourcesOf(c, 'google_ads')[0])).toBe('111');
  });

  it('un cliente sin fuentes no rompe', () => {
    expect(adSourcesOf(client(), 'google_ads')).toEqual([]);
    expect(adSourcesOf(client(), 'meta_ads')).toEqual([]);
  });

  it('tolera un registro antiguo sin las listas', () => {
    const legacy = { ...client(), google_ads_sources: undefined, meta_ads_sources: undefined } as unknown as ApiClient;
    expect(adSourcesOf(legacy, 'google_ads')).toEqual([]);
    expect(adSourcesOf(legacy, 'meta_ads')).toEqual([]);
  });
});

describe('cuenta principal', () => {
  it('es la fuente que consume la cuenta entera', () => {
    const c = client({ google_ads_sources: [gads({ customer_id: '111', campaign_ids: [] })] });
    expect(primaryAccountId(c, 'google_ads')).toBe('111');
  });

  it('queda vacía si el cliente solo consume campañas de la cuenta de otro', () => {
    const c = client({
      google_ads_sources: [gads({ customer_id: '5276108056', campaign_ids: ['201', '202'] })],
    });
    // No se presenta la cuenta ajena como si fuera suya.
    expect(primaryAccountId(c, 'google_ads')).toBe('');
  });

  it('encuentra la cuenta entera aunque no sea la primera de la lista', () => {
    const c = client({
      google_ads_sources: [
        gads({ customer_id: '5276108056', campaign_ids: ['201'] }),
        gads({ customer_id: '999', campaign_ids: [] }),
      ],
    });
    expect(primaryAccountId(c, 'google_ads')).toBe('999');
  });

  it('funciona igual en Meta', () => {
    expect(primaryAccountId(client({ meta_ads_sources: [meta({ ad_account_id: '8', campaign_ids: [] })] }), 'meta_ads')).toBe('8');
    expect(primaryAccountId(client({ meta_ads_sources: [meta({ ad_account_id: '8', campaign_ids: ['601'] })] }), 'meta_ads')).toBe('');
  });

  it('una fuente Meta solo con ad sets es parcial, no cuenta principal', () => {
    const partial = meta({
      ad_account_id: '8',
      adset_selections: [{ campaign_id: '601', adset_ids: ['6011'], allocation: 'exclusive' }],
    });
    expect(primaryAccountId(client({ meta_ads_sources: [partial] }), 'meta_ads')).toBe('');
  });

  it('sin fuentes no hay cuenta principal', () => {
    expect(primaryAccountId(client(), 'google_ads')).toBe('');
    expect(primaryAccountId(client(), 'meta_ads')).toBe('');
  });
});

describe('detección de cambios de fuentes', () => {
  it('detecta cambios solo en selecciones de ad sets', () => {
    const before = [meta({
      adset_selections: [{ campaign_id: '601', adset_ids: ['6011'], allocation: 'exclusive' }],
    })];
    const after = [meta({
      adset_selections: [{ campaign_id: '601', adset_ids: ['6011', '6012'], allocation: 'exclusive' }],
    })];
    expect(sameSources(before, after)).toBe(false);
  });

  it('detecta cambios del MCC de acceso de Google Ads', () => {
    expect(sameSources(
      [gads({ login_customer_id: '111' })],
      [gads({ login_customer_id: '222' })],
    )).toBe(false);
  });
});

describe('alcance consolidado de Meta', () => {
  it('la métrica de varias cuentas deja explícito que es una suma', () => {
    expect(METRICS.meta_reach_sum.label).toBe('Alcance (suma de cuentas)');
    expect(METRICS.meta_reach_sum.key).toBe('reach');
  });

  it('con una sola cuenta se muestra como alcance a secas', () => {
    expect(METRICS.meta_reach.label).toBe('Alcance');
  });
});
