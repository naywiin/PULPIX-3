import { useEffect, useMemo, useState } from 'react';
import { ClientSelector } from './components/ClientSelector';
import { CompareSelect } from './components/CompareSelect';
import { DataSourceBadge } from './components/DataSourceBadge';
import { isSyntheticPreview } from './services/dataSource';
import { DateRangePicker } from './components/DateRangePicker';
import { ReportHeader } from './components/ReportHeader';
import { ReportingActions } from './components/ReportingActions';
import { Sidebar, type PageId } from './components/Sidebar';
import { EmptyState, ErrorState } from './components/States';
import { useClients } from './hooks/useClients';
import { useDashboard } from './hooks/useDashboard';
import { defaultSelection, todayISO } from './lib/dates';
import { SettingsClients } from './pages/SettingsClients';
import { detectDataSource, type SourceDecision } from './services/dataSource';
import { AnalyticsSection } from './sections/AnalyticsSection';
import { GoogleAdsSection } from './sections/GoogleAdsSection';
import { MetaAdsSection } from './sections/MetaAdsSection';
import { OverviewSection } from './sections/OverviewSection';
import { SalesSection } from './sections/SalesSection';
import { SpotifyAdsSection } from './sections/SpotifyAdsSection';
import { PinterestAdsSection } from './sections/PinterestAdsSection';
import type { DateSelection } from './types/report';
import { hasWooSyncSales } from './config/woosync';

const PAGE_TITLES: Record<PageId, string> = {
  overview: 'Overview',
  'google-ads': 'Google Ads',
  'meta-ads': 'Meta Ads',
  'spotify-ads': 'Spotify Ads',
  'pinterest-ads': 'Pinterest Ads',
  analytics: 'Google Analytics',
  sales: 'Ventas',
  'settings-clients': 'Settings · Clientes',
};

const readHashPage = (): PageId => {
  const h = window.location.hash.replace('#/', '') as PageId;
  return h in PAGE_TITLES ? h : 'overview';
};

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

const timeFmt = new Intl.DateTimeFormat('es-MX', { hour: '2-digit', minute: '2-digit' });

export default function App() {
  const today = useMemo(() => todayISO(), []);
  const [decision, setDecision] = useState<SourceDecision | null>(null);
  const [clientId, setClientId] = useState(() => readStored('mp.client') ?? '');
  const [selection, setSelection] = useState<DateSelection>(() => defaultSelection(today));
  const [page, setPage] = useState<PageId>(readHashPage);
  const [navOpen, setNavOpen] = useState(false);
  const [reportingRefreshNonce, setReportingRefreshNonce] = useState(0);

  useEffect(() => {
    detectDataSource().then(setDecision);
  }, []);

  const source = decision?.source ?? null;
  const clients = useClients(source);
  const client = clients.clients.find((c) => c.id === clientId) ?? clients.clients[0] ?? null;
  const showSales = hasWooSyncSales(client?.id);
  // La sección se mantiene disponible para mostrar estado y permitir al usuario
  // ir a Clientes a configurar Spotify, aunque todavía no haya vínculo.
  const showSpotify = true;
  const dash = useDashboard(page === 'settings-clients' || page === 'sales' ? null : source, client, selection, today);
  const isSettings = page === 'settings-clients';

  useEffect(() => {
    const onHash = () => setPage(readHashPage());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    if (!client) return;
    try {
      localStorage.setItem('mp.client', client.id);
    } catch {
      /* almacenamiento no disponible */
    }
  }, [client]);

  useEffect(() => {
    document.title = `${client && !isSettings ? `${client.name} · ` : ''}${PAGE_TITLES[page]} — Performance Report`;
  }, [client, page, isSettings]);

  const navigate = (p: PageId) => {
    window.location.hash = `/${p}`;
    setPage(p);
    window.scrollTo({ top: 0 });
  };

  useEffect(() => {
    if (page === 'sales' && client && !hasWooSyncSales(client.id)) navigate('overview');
  }, [client?.id, page]);

  // Los datos en pantalla deben corresponder al cliente seleccionado; si no, se tratan como cargando.
  const data = dash.data && client && dash.data.clientId === client.id ? dash.data : null;
  const loading = !data && (dash.status === 'loading' || dash.status === 'idle');
  const viewSource = data?.source ?? source;
  const common = { client: client!, selection, loading, refreshing: dash.refreshing, onRetry: dash.retry };

  const sidebarFooter =
    isSyntheticPreview()
      ? 'Vista previa local con datos sintéticos. No usa datos productivos.'
      : source === 'real'
      ? `Datos vía backend. Actualización automática cada 5 min.${decision?.metaConfigured === false ? ' Meta Ads: falta configurar el token en el servidor.' : ''}`
      : source === 'mock'
        ? 'MOCK DATA: datos de demostración locales. Inicia el backend para ver datos reales.'
        : 'Conectando…';

  let content: React.ReactNode;
  if (!decision || clients.status === 'loading') content = <div className="card state muted">Conectando con el backend…</div>;
  else if (clients.status === 'error')
    content = <ErrorState message={`No se pudo cargar el registro de clientes: ${clients.error}`} onRetry={clients.reload} />;
  else if (isSettings) content = <SettingsClients registry={clients.registry} backendUp={source === 'real' && decision.backendUp} onChanged={clients.reload} />;
  else if (!client)
    content = <EmptyState title="Sin clientes activos" message="Agrega o activa un cliente en Settings → Clientes." />;
  else
    content = (
      <>
        <ReportHeader client={client} selection={selection} pageTitle={PAGE_TITLES[page]} />
        {dash.status === 'error' && !data ? (
          <ErrorState message={dash.error ?? 'Error desconocido'} onRetry={dash.retry} />
        ) : (
          // key por cliente: resetea búsquedas/paginación al cambiar de cliente
          <div key={client.id}>
            {page === 'overview' && <OverviewSection {...common} data={data} reportingRefreshNonce={reportingRefreshNonce} reportingClient={clients.registry.find((item) => item.id === client.id) ?? null} isRealBackend={source === 'real'} />}
            {page === 'google-ads' && <GoogleAdsSection {...common} block={data?.google_ads ?? null} />}
            {page === 'meta-ads' && <MetaAdsSection {...common} block={data?.meta_ads ?? null} />}
            {page === 'spotify-ads' && showSpotify && <SpotifyAdsSection {...common} block={data?.spotify_ads ?? null} />}
            {page === 'pinterest-ads' && <PinterestAdsSection {...common} block={data?.pinterest_ads ?? null} />}
            {page === 'analytics' && <AnalyticsSection {...common} block={data?.ga4 ?? null} />}
            {page === 'sales' && showSales && <SalesSection client={client} selection={selection} onSelectionChange={setSelection} />}
          </div>
        )}
        <footer className="report-foot muted small">
          Fuente: {page === 'sales' ? 'WooSync Analytics vía backend' : viewSource === 'real' ? `APIs de Google Ads, Meta Ads${showSpotify ? ', Spotify Ads' : ''}, Pinterest Ads y GA4 vía backend` : 'datos mock locales (desarrollo)'} · Zona horaria{' '}
          {client.timezone}
        </footer>
      </>
    );

  return (
    <div className="layout">
      <Sidebar page={page} onNavigate={navigate} open={navOpen} onClose={() => setNavOpen(false)} footer={sidebarFooter} showSales={showSales} showSpotify={showSpotify} />
      <div className="main">
        {viewSource === 'mock' && (
          <div className="mock-banner" role="status">
            <strong>MOCK DATA</strong> · {decision?.reason} Ningún número de esta vista proviene de las plataformas reales.
          </div>
        )}
        <header className="topbar">
          <button type="button" className="btn-icon menu-btn" onClick={() => setNavOpen(true)} aria-label="Abrir navegación">
            ☰
          </button>
          {!isSettings && <ClientSelector clients={clients.clients} value={client?.id ?? ''} onChange={setClientId} />}
          <div className="topbar-spacer" />
          {!isSettings && page !== 'sales' && (
            <>
              <DateRangePicker value={selection} today={today} onChange={setSelection} />
              <CompareSelect value={selection} onChange={setSelection} />
              <div className="refresh-box">
                <button type="button" className="btn" onClick={() => { dash.refresh(); setReportingRefreshNonce((value) => value + 1); }} disabled={!client || dash.status === 'loading' || dash.refreshing}>
                  <span aria-hidden className={dash.refreshing ? 'spin' : undefined}>
                    ⟳
                  </span>{' '}
                  Actualizar
                </button>
                <span className="muted small last-update">
                  Última actualización: {dash.lastUpdated ? timeFmt.format(dash.lastUpdated) : '—'}
                </span>
              </div>
            </>
          )}
          {!isSettings && source === 'real' && decision?.backendUp && (
            <ReportingActions range={selection.range} />
          )}
          <DataSourceBadge source={viewSource} title={decision?.reason} />
        </header>

        <main className="content">{content}</main>
      </div>
    </div>
  );
}
