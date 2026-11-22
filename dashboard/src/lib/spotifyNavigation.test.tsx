import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Sidebar } from '../components/Sidebar';
import { SettingsClients } from '../pages/SettingsClients';

describe('Spotify Ads navigation', () => {
  it('keeps the Spotify report entry available for unconfigured clients', () => {
    const props = {
      page: 'overview' as const,
      onNavigate: () => {},
      open: false,
      onClose: () => {},
      footer: '',
      showSales: false,
    };
    expect(renderToStaticMarkup(<Sidebar {...props} showSpotify />)).toContain('Spotify Ads');
  });

  it('offers Spotify configuration and report ordering in Settings without an existing source', () => {
    const client = {
      id: 'sample', name: 'Sample Client', slug: 'sample', logo: null, active: true,
      google_ads_customer_id: null, google_ads_login_customer_id: null, meta_ad_account_id: null,
      google_ads_sources: [], meta_ads_sources: [], spotify_ads_sources: [],
      ga4_property_id: null, ga4_property_ids: [], ga4_account_id: null,
      currency: 'MXN', timezone: 'America/Mexico_City', brand_color: null, notes: null,
      hidden_metrics: [], report_category: null, report_position: null,
    };
    const markup = renderToStaticMarkup(<SettingsClients registry={[client]} backendUp onChanged={() => {}} />);
    expect(markup).toContain('Spotify Ads · fuente');
    expect(markup).toContain('No configurado');
    expect(markup).toContain('Categoría Excel');
    expect(markup).toContain('Posición en el Excel');
    expect(markup).toContain('B2B / CORPORATIVO');
    expect(markup).toContain('Pinterest Ads · fuente');
    expect(markup).toContain('No configurado');
    expect(renderToStaticMarkup(<Sidebar page="overview" onNavigate={() => {}} open={false} onClose={() => {}} footer="" showSales={false} showSpotify />)).toContain('Pinterest Ads');
  });
});
