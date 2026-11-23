import { useCallback, useEffect, useState } from 'react';
import { api, type IntegrationStatusMap } from '../api/backend';
import type { DiscoveredAccount } from '../types/dashboard';

interface Props {
  backendUp: boolean;
  onClientsImported: () => void;
}

interface SelectableAccount {
  key: string;
  platform: 'meta_ads' | 'google_ads';
  account_id: string;
  name: string;
  currency: string;
  timezone: string;
  login_customer_id?: string | null;
}

export function IntegrationsPanel({ backendUp, onClientsImported }: Props) {
  const [status, setStatus] = useState<IntegrationStatusMap | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyProvider, setBusyProvider] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [discovering, setDiscovering] = useState(false);
  const [accounts, setAccounts] = useState<SelectableAccount[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  const loadStatus = useCallback(async () => {
    if (!backendUp) return;
    setLoading(true);
    try {
      const next = await api.integrationStatus();
      setStatus(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo consultar el estado de las conexiones.');
    } finally {
      setLoading(false);
    }
  }, [backendUp]);

  useEffect(() => {
    loadStatus();
    // Mostrar confirmación si el usuario acaba de volver de OAuth
    const hash = window.location.hash;
    const queryIdx = hash.indexOf('?');
    if (queryIdx !== -1) {
      const params = new URLSearchParams(hash.slice(queryIdx + 1));
      const connected = params.get('connected');
      const oauthError = params.get('oauth_error');
      if (connected === 'meta_ads') setMessage('Meta Ads conectado correctamente. Ya puedes jalar tus cuentas.');
      if (connected === 'google_ads') setMessage('Google Ads conectado correctamente. Ya puedes jalar tus cuentas.');
      if (oauthError) setError(`Error al conectar cuenta: ${oauthError}`);
      window.history.replaceState(null, '', hash.slice(0, queryIdx));
    }
  }, [loadStatus]);

  const handleConnect = async (provider: 'meta_ads' | 'google_ads') => {
    setError(null);
    setMessage(null);
    setBusyProvider(provider);
    try {
      const res = provider === 'meta_ads' ? await api.startMetaOAuth() : await api.startGoogleAdsOAuth();
      window.location.href = res.auth_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo iniciar la conexión.');
      setBusyProvider(null);
    }
  };

  const handleDisconnect = async (provider: 'meta_ads' | 'google_ads') => {
    setError(null);
    setMessage(null);
    setBusyProvider(provider);
    try {
      if (provider === 'meta_ads') await api.disconnectMeta();
      else await api.disconnectGoogleAds();
      setMessage(provider === 'meta_ads' ? 'Meta Ads desconectado.' : 'Google Ads desconectado.');
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo desconectar.');
    } finally {
      setBusyProvider(null);
    }
  };

  const handleDiscover = async () => {
    setError(null);
    setMessage(null);
    setDiscovering(true);
    try {
      const found: SelectableAccount[] = [];
      const tasks: Promise<void>[] = [];
      if (status?.meta_ads?.connected) {
        tasks.push(
          api.discover('meta', true).then((res) => {
            for (const acc of res.accounts as DiscoveredAccount[]) {
              if (!acc.id) continue;
              found.push({
                key: `meta_ads:${acc.id}`,
                platform: 'meta_ads',
                account_id: acc.id,
                name: acc.name || `Meta ${acc.id}`,
                currency: acc.currency || 'MXN',
                timezone: acc.timezone || 'America/Mexico_City',
              });
            }
          }),
        );
      }
      if (status?.google_ads?.connected) {
        tasks.push(
          api.discover('google-ads', true).then((res) => {
            for (const acc of res.accounts as DiscoveredAccount[]) {
              if (!acc.id || acc.account_type === 'manager') continue;
              found.push({
                key: `google_ads:${acc.id}`,
                platform: 'google_ads',
                account_id: acc.id,
                name: acc.name || `Google Ads ${acc.id}`,
                currency: acc.currency || 'MXN',
                timezone: acc.timezone || 'America/Mexico_City',
                login_customer_id: acc.login_customer_id ?? null,
              });
            }
          }),
        );
      }
      await Promise.all(tasks);
      setAccounts(found);
      setSelected(new Set(found.map((a) => a.key)));
      if (found.length === 0) {
        setMessage('No se encontraron cuentas publicitarias en las plataformas conectadas.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudieron descubrir las cuentas.');
    } finally {
      setDiscovering(false);
    }
  };

  const toggleKey = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleImport = async () => {
    const toImport = accounts.filter((a) => selected.has(a.key));
    if (toImport.length === 0) return;
    setImporting(true);
    setError(null);
    setMessage(null);
    try {
      const res = await api.importClients(toImport);
      setMessage(
        `Clientes importados: ${res.created.length}${res.skipped.length ? ` · Omitidos (ya existentes): ${res.skipped.length}` : ''}. Ya puedes asignar o mover campañas abajo.`,
      );
      setAccounts([]);
      setSelected(new Set());
      onClientsImported();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudieron importar los clientes.');
    } finally {
      setImporting(false);
    }
  };

  if (!backendUp) return null;

  const meta = status?.meta_ads;
  const gads = status?.google_ads;
  const anyConnected = Boolean(meta?.connected || gads?.connected);

  return (
    <section className="card" style={{ marginBottom: 20, padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Conexiones de cuentas publicitarias</h2>
          <p className="muted small" style={{ margin: '4px 0 0' }}>
            Conecta tu Facebook (Meta Ads) y tu Google Ads para jalar automáticamente tus clientes y asignar campañas.
          </p>
        </div>
        {anyConnected && (
          <button type="button" className="btn btn-primary" onClick={handleDiscover} disabled={discovering}>
            {discovering ? 'Buscando cuentas…' : 'Jalar cuentas como clientes'}
          </button>
        )}
      </div>

      {error && (
        <div role="alert" style={{ color: '#b91c1c', marginTop: 12, fontSize: '0.875rem' }}>
          {error}
        </div>
      )}
      {message && (
        <div role="status" style={{ color: '#0f766e', marginTop: 12, fontSize: '0.875rem' }}>
          {message}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12, marginTop: 16 }}>
        <div style={{ border: '1px solid var(--border, #e2e8f0)', borderRadius: 10, padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <strong>Meta Ads (Facebook)</strong>
            <span className="small muted">{loading ? '…' : meta?.connected ? 'Conectado' : 'Sin conectar'}</span>
          </div>
          <p className="muted small" style={{ margin: '6px 0 12px' }}>
            {meta?.connected
              ? `Cuenta: ${meta.account_label ?? 'Conectada'}`
              : 'Autoriza lectura de tus cuentas publicitarias en Business Manager.'}
          </p>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="btn"
              onClick={() => handleConnect('meta_ads')}
              disabled={busyProvider !== null}
            >
              {meta?.connected ? 'Reconectar Meta' : 'Conectar Meta Ads'}
            </button>
            {meta?.connected && (
              <button
                type="button"
                className="btn"
                onClick={() => handleDisconnect('meta_ads')}
                disabled={busyProvider !== null}
              >
                Desconectar
              </button>
            )}
          </div>
        </div>

        <div style={{ border: '1px solid var(--border, #e2e8f0)', borderRadius: 10, padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <strong>Google Ads</strong>
            <span className="small muted">{loading ? '…' : gads?.connected ? 'Conectado' : 'Sin conectar'}</span>
          </div>
          <p className="muted small" style={{ margin: '6px 0 12px' }}>
            {gads?.connected
              ? `Cuenta: ${gads.account_label ?? 'Conectada'}`
              : 'Autoriza lectura de tus MCC y cuentas cliente de Google Ads.'}
          </p>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="btn"
              onClick={() => handleConnect('google_ads')}
              disabled={busyProvider !== null}
            >
              {gads?.connected ? 'Reconectar Google Ads' : 'Conectar Google Ads'}
            </button>
            {gads?.connected && (
              <button
                type="button"
                className="btn"
                onClick={() => handleDisconnect('google_ads')}
                disabled={busyProvider !== null}
              >
                Desconectar
              </button>
            )}
          </div>
        </div>
      </div>

      {accounts.length > 0 && (
        <div style={{ marginTop: 16, borderTop: '1px solid var(--border, #e2e8f0)', paddingTop: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <strong>Cuentas descubiertas ({accounts.length})</strong>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleImport}
              disabled={importing || selected.size === 0}
            >
              {importing ? 'Importando…' : `Importar seleccionadas (${selected.size})`}
            </button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 8, maxHeight: 240, overflowY: 'auto' }}>
            {accounts.map((acc) => (
              <label
                key={acc.key}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 10px',
                  border: '1px solid var(--border, #e2e8f0)',
                  borderRadius: 8,
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={selected.has(acc.key)}
                  onChange={() => toggleKey(acc.key)}
                />
                <span className="small">
                  <strong>{acc.name}</strong>{' '}
                  <span className="muted">
                    ({acc.platform === 'meta_ads' ? 'Meta' : 'Google Ads'} · {acc.account_id})
                  </span>
                </span>
              </label>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
