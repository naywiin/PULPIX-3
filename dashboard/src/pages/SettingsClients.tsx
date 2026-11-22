import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api/backend';
import { EmptyState } from '../components/States';
import { CampaignSourceEditor } from '../components/CampaignSourceEditor';
import { PropertyMultiSelect, summaryLabel } from '../components/PropertyMultiSelect';
import { isExcluded, withoutExcluded } from '../config/exclusions';
import type { AdSource, ApiClient, DiscoveredAccount, DiscoveryResult, GoogleAdsSource, MetaAdsSource, SpotifyAdsSource, PinterestAdsSource } from '../types/dashboard';
import { isWholeAccountSource, sourceAccountId } from '../types/dashboard';

type PlatformKey = 'google_ads' | 'meta_ads' | 'spotify_ads' | 'pinterest_ads' | 'ga4';

interface PlatformDef {
  key: PlatformKey;
  label: string;
  route: 'google-ads' | 'meta' | 'spotify-ads' | 'pinterest-ads' | 'ga4';
  field: keyof ApiClient;
  noun: string;
  /** GA4 admite varias cuentas por cliente: se edita con un multiselect. */
  multi?: boolean;
}

const PLATFORMS: PlatformDef[] = [
  { key: 'google_ads', label: 'Google Ads', route: 'google-ads', field: 'google_ads_customer_id', noun: 'cuenta' },
  { key: 'meta_ads', label: 'Meta Ads', route: 'meta', field: 'meta_ad_account_id', noun: 'cuenta' },
  { key: 'spotify_ads', label: 'Spotify Ads', route: 'spotify-ads', field: 'spotify_ads_sources', noun: 'cuenta' },
  { key: 'pinterest_ads', label: 'Pinterest Ads', route: 'pinterest-ads', field: 'pinterest_ads_sources', noun: 'cuenta' },
  { key: 'ga4', label: 'Google Analytics', route: 'ga4', field: 'ga4_property_id', noun: 'propiedad', multi: true },
];

const REPORT_CATEGORIES = ['ENTRETENIMIENTO', 'B2B / CORPORATIVO', 'ECOMMERCE', 'GENERAL'] as const;

/** Propiedades GA4 del cliente, tolerando registros antiguos con un solo ID. */
export function ga4Ids(c: Pick<ApiClient, 'ga4_property_ids' | 'ga4_property_id'>): string[] {
  if (c.ga4_property_ids?.length) return c.ga4_property_ids;
  return c.ga4_property_id ? [c.ga4_property_id] : [];
}

const sameIds = (a: string[], b: string[]) => a.length === b.length && a.every((v, i) => v === b[i]);

/** Fuentes de campañas del cliente en una plataforma de anuncios. */
export function adSourcesOf(c: ApiClient, platform: 'google_ads' | 'meta_ads'): AdSource[] {
  return (platform === 'google_ads' ? c.google_ads_sources : c.meta_ads_sources) ?? [];
}

const gadsSources = (c: ApiClient): GoogleAdsSource[] => c.google_ads_sources ?? [];
const metaSources = (c: ApiClient): MetaAdsSource[] => c.meta_ads_sources ?? [];
const spotifySources = (c: ApiClient): SpotifyAdsSource[] => c.spotify_ads_sources ?? [];
const pinterestSources = (c: ApiClient): PinterestAdsSource[] => c.pinterest_ads_sources ?? [];
const sameSpotifySources = (a: SpotifyAdsSource[], b: SpotifyAdsSource[]) =>
  a.length === b.length && a.every((source, index) => source.business_id === b[index]?.business_id && source.ad_account_id === b[index]?.ad_account_id);
const samePinterestSources = (a: PinterestAdsSource[], b: PinterestAdsSource[]) =>
  a.length === b.length && a.every((source, index) => source.source_id === b[index]?.source_id && source.ad_account_id === b[index]?.ad_account_id && source.currency === b[index]?.currency);

/**
 * La CUENTA PRINCIPAL es la primera fuente que toma la cuenta entera (sin
 * campañas seleccionadas). Un cliente que solo consume campañas sueltas de la
 * cuenta de otro NO tiene cuenta principal: el selector queda vacío en vez de
 * presentar la cuenta ajena como suya.
 */
export function primaryAccountId(c: ApiClient, platform: 'google_ads' | 'meta_ads'): string {
  const whole = adSourcesOf(c, platform).find(isWholeAccountSource);
  return whole ? sourceAccountId(whole) : '';
}

/** Índice de la fuente que es la cuenta principal, o -1. */
const primaryIndex = (sources: AdSource[]) => sources.findIndex(isWholeAccountSource);

const sameAdsetSelections = (a: MetaAdsSource['adset_selections'], b: MetaAdsSource['adset_selections']) => {
  const left = a ?? [];
  const right = b ?? [];
  return left.length === right.length && left.every((selection, i) => {
    const other = right[i];
    return selection.campaign_id === other.campaign_id &&
      selection.allocation === other.allocation &&
      sameIds(selection.adset_ids, other.adset_ids);
  });
};

/** Comparación por valor: el draft guarda objetos nuevos en cada edición. */
export const sameSources = (a: AdSource[], b: AdSource[]) =>
  a.length === b.length &&
  a.every((s, i) => {
    const o = b[i];
    return (
      sourceAccountId(s) === sourceAccountId(o) &&
      s.allocation === o.allocation &&
      sameIds(s.campaign_ids, o.campaign_ids) &&
      ('customer_id' in s && 'customer_id' in o
        ? s.login_customer_id === o.login_customer_id
        : 'ad_account_id' in s && 'ad_account_id' in o &&
          sameAdsetSelections(s.adset_selections, o.adset_selections))
    );
  });

type Discovery = Partial<Record<PlatformKey, DiscoveryResult | { status: 'loading' }>>;

interface Props {
  registry: ApiClient[];
  backendUp: boolean;
  onChanged: () => void;
}

export function SettingsClients({ registry, backendUp, onChanged }: Props) {
  const [discovery, setDiscovery] = useState<Discovery>({});
  const [filter, setFilter] = useState<'all' | 'active' | 'pending'>('all');
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState(false);

  const discover = useCallback((refresh: boolean) => {
    for (const p of PLATFORMS) {
      setDiscovery((d) => ({ ...d, [p.key]: { status: 'loading' } }));
      api
        .discover(p.route, refresh)
        .then((res) =>
          setDiscovery((d) => ({
            // El backend ya excluye; se repite aquí para que nada excluido
            // pueda llegar a un selector ni al volver a "Descubrir cuentas".
            ...d,
            [p.key]: { ...res, accounts: withoutExcluded(res.accounts, (a) => [a.name, a.account_name]) },
          })),
        )
        .catch((e: unknown) =>
          setDiscovery((d) => ({ ...d, [p.key]: { platform: p.key, status: 'error', message: `${p.label} temporalmente no disponible`, detail: e instanceof Error ? e.message : String(e), accounts: [] } })),
        );
    }
  }, []);

  // Carga inicial desde la cache del backend (no fuerza llamadas a las APIs).
  useEffect(() => {
    if (backendUp) discover(false);
  }, [backendUp, discover]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return registry.filter((c) => {
      if (isExcluded(c.name, c.slug, c.id)) return false;
      if (filter === 'active' && !c.active) return false;
      if (filter === 'pending' && c.google_ads_customer_id && c.meta_ad_account_id && ga4Ids(c).length > 0 && spotifySources(c).length > 0 && pinterestSources(c).length > 0) return false;
      return !q || c.name.toLowerCase().includes(q) || c.slug.includes(q);
    });
  }, [registry, filter, query]);

  if (!backendUp)
    return (
      <section className="report-section">
        <EmptyState
          title="Configuración no disponible en modo MOCK"
          message="El registro de clientes y el descubrimiento de cuentas viven en el backend. Inicia el backend (ver backend/README.md) y recarga la página."
        />
      </section>
    );

  return (
    <section className="report-section settings">
      <div className="settings-head card">
        <div>
          <h2>Clientes</h2>
          <p className="muted small">
            Registro central de clientes. Cada cliente se asocia a lo sumo a una cuenta por plataforma y una cuenta no puede pertenecer a dos clientes.
          </p>
        </div>
        <div className="settings-actions">
          <button type="button" className="btn" onClick={() => discover(true)}>
            ⟳ Descubrir cuentas
          </button>
          <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
            + Agregar cliente
          </button>
        </div>
      </div>

      <div className="discovery-status">
        {PLATFORMS.map((p) => {
          const d = discovery[p.key];
          let text = 'Sin consultar';
          let cls = 'neutral';
          if (d?.status === 'loading') text = 'Consultando…';
          else if (d?.status === 'ok') {
            text = `${d.accounts.length} ${p.noun === 'cuenta' ? 'cuentas' : 'propiedades'}`;
            // Google Ads: la jerarquía del MCC trae anunciantes y MCC secundarios.
            if (d.summary && d.summary.managers > 0)
              text = `${d.summary.advertisers} anunciantes · ${d.summary.managers} MCC`;
            cls = 'ok';
          } else if (d) {
            text = d.detail ?? d.message ?? 'No disponible';
            cls = 'warn';
          }
          return (
            <div key={p.key} className={`disc-pill disc-${cls}`} title={text}>
              <strong>{p.label}</strong> <span>{text}</span>
            </div>
          );
        })}
      </div>

      {adding && (
        <ClientEditor
          title="Nuevo cliente"
          onCancel={() => setAdding(false)}
          onSave={async (data) => {
            await api.createClient(data as Partial<ApiClient> & { name: string });
            setAdding(false);
            onChanged();
          }}
        />
      )}

      <div className="settings-filters">
        <input type="search" className="input search" placeholder="Buscar cliente…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Buscar cliente" />
        <select className="input" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} aria-label="Filtrar clientes">
          <option value="all">Todos ({registry.length})</option>
          <option value="active">Activos</option>
          <option value="pending">Con asignaciones pendientes</option>
        </select>
      </div>

      {visible.length === 0 ? (
        <EmptyState title="Sin clientes" message="No hay clientes que coincidan con el filtro." />
      ) : (
        visible.map((c) => <ClientRow key={c.id} client={c} registry={registry} discovery={discovery} onSaved={onChanged} />)
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ fila */

function accountLabel(a: DiscoveredAccount): string {
  const parts = [a.name ?? '(sin nombre)', a.id];
  if (a.account_name) parts.push(a.account_name);
  if (a.currency) parts.push(a.currency);
  if (a.status && !['ENABLED', 'ACTIVE'].includes(a.status)) parts.push(a.status);
  if (a.account_type === 'manager') parts.push('MCC');
  if (a.level !== undefined && a.level >= 2) parts.push('indirecta');
  return parts.join(' · ');
}

function accountOption(a: DiscoveredAccount, clientId: string) {
  const taken = a.assigned_to && a.assigned_to.id !== clientId;
  const reserved = a.reserved_for && a.reserved_for.id !== clientId;
  return (
    <option key={a.id} value={a.id} disabled={Boolean(taken || reserved)}>
      {accountLabel(a)}
      {taken ? ` — asignada a ${a.assigned_to!.name}` : ''}
      {!taken && reserved ? ` — disponible solo para ${a.reserved_for!.name}` : ''}
    </option>
  );
}

function ClientRow({ client, registry, discovery, onSaved }: { client: ApiClient; registry: ApiClient[]; discovery: Discovery; onSaved: () => void }) {
  const [draft, setDraft] = useState(client);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => setDraft(client), [client]);

  const dirty =
    PLATFORMS.some((p) => !p.multi && !['spotify_ads', 'pinterest_ads'].includes(p.key) && draft[p.field] !== client[p.field]) ||
    !sameIds(ga4Ids(draft), ga4Ids(client)) ||
    !sameSources(adSourcesOf(draft, 'google_ads'), adSourcesOf(client, 'google_ads')) ||
    !sameSources(adSourcesOf(draft, 'meta_ads'), adSourcesOf(client, 'meta_ads')) ||
    !sameSpotifySources(spotifySources(draft), spotifySources(client)) ||
    !samePinterestSources(pinterestSources(draft), pinterestSources(client)) ||
    draft.active !== client.active || draft.report_category !== client.report_category || draft.report_position !== client.report_position;

  const save = async (patch: Partial<ApiClient>) => {
    setSaving(true);
    setError(null);
    try {
      await api.updateClient(client.id, patch);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const ga4Accounts = discovery.ga4?.status === 'ok' ? discovery.ga4.accounts : [];

  const setAccount = (p: PlatformDef, id: string) => {
    const value = id || null;
    const next = { ...draft, [p.field]: value };
    if (p.key === 'google_ads') {
      const sources = gadsSources(draft);
      const i = primaryIndex(sources);
      // Solo se reemplaza la fuente de cuenta completa: las parciales (campañas
      // tomadas de cuentas de otros clientes) son independientes y se conservan.
      const rest = sources.filter((_, idx) => idx !== i);
      const d = discovery.google_ads;
      const acc = d && d.status === 'ok' ? d.accounts.find((a) => a.id === id) : undefined;
      const previous = i >= 0 ? sources[i] : undefined;
      const login = acc?.login_customer_id ?? (previous && previous.customer_id === id ? previous.login_customer_id : null);
      next.google_ads_login_customer_id = value ? login : null;
      next.google_ads_sources = value
        ? [{ customer_id: value, login_customer_id: login, campaign_ids: [], allocation: 'exclusive' }, ...rest]
        : rest;
    } else if (p.key === 'meta_ads') {
      const sources = metaSources(draft);
      const i = primaryIndex(sources);
      const rest = sources.filter((_, idx) => idx !== i);
      next.meta_ads_sources = value
        ? [{ ad_account_id: value, campaign_ids: [], allocation: 'exclusive' }, ...rest]
        : rest;
    } else if (p.key === 'spotify_ads') {
      const account = discovery.spotify_ads?.status === 'ok' ? discovery.spotify_ads.accounts.find((item) => item.id === id) : undefined;
      next.spotify_ads_sources = id && account?.business_id
        ? [{ business_id: account.business_id, ad_account_id: id }]
        : [];
    } else if (p.key === 'pinterest_ads') {
      const account = discovery.pinterest_ads?.status === 'ok' ? discovery.pinterest_ads.accounts.find((item) => item.id === id) : undefined;
      next.pinterest_ads_sources = id && account ? [{
        source_id: id, ad_account_id: id, display_name: account.name, currency: account.currency,
        status: account.status, campaign_ids: [], allocation: 'exclusive',
      }] : [];
    }
    setDraft(next);
  };

  const setSources = (platform: 'google_ads' | 'meta_ads', sources: AdSource[]) =>
    setDraft((d) => {
      // El campo suelto solo refleja una cuenta COMPLETA; si el cliente solo
      // tiene asignaciones parciales, queda vacío.
      const i = primaryIndex(sources);
      const primary = i >= 0 ? sourceAccountId(sources[i]) : null;
      return platform === 'google_ads'
        ? { ...d, google_ads_sources: sources as GoogleAdsSource[], google_ads_customer_id: primary }
        : { ...d, meta_ads_sources: sources as MetaAdsSource[], meta_ad_account_id: primary };
    });

  /** GA4: N propiedades. ga4_property_id se mantiene como la primera (compatibilidad). */
  const setGa4 = (ids: string[]) => {
    // La cuenta contenedora es informativa: se toma la de la primera propiedad.
    const first = ga4Accounts.find((a) => a.id === ids[0]);
    setDraft({
      ...draft,
      ga4_property_ids: ids,
      ga4_property_id: ids[0] ?? null,
      ga4_account_id: ids.length === 0 ? null : (first?.account_id ?? draft.ga4_account_id),
    });
  };

  const initials = client.name.slice(0, 2).toUpperCase();
  const category = draft.report_category ?? 'GENERAL';
  const duplicatePosition = draft.report_position != null && registry.some((other) => other.id !== client.id && other.active &&
    (other.report_category ?? 'GENERAL') === category && other.report_position === draft.report_position);

  return (
    <article className={`card client-row${client.active ? '' : ' inactive'}`}>
      <header className="client-row-head">
        {client.logo ? (
          <img className="client-logo sm" src={client.logo} alt="" />
        ) : (
          <div className="client-logo sm" style={{ background: client.brand_color ?? 'var(--muted)' }} aria-hidden>
            {initials}
          </div>
        )}
        <div className="client-row-title">
          <h3>{client.name}</h3>
          <span className="muted small">
            {client.slug} · {client.currency} · {client.timezone}
          </span>
        </div>
        <label className="switch">
          <input type="checkbox" checked={draft.active} onChange={(e) => setDraft({ ...draft, active: e.target.checked })} />
          <span>{draft.active ? 'Activo' : 'Inactivo'}</span>
        </label>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
          {editing ? 'Cerrar' : 'Editar'}
        </button>
      </header>

      {/* Cuenta principal de cada plataforma: tres columnas estrechas. */}
      <div className="mapping-grid">
        {PLATFORMS.map((p) => {
          const d = discovery[p.key];
          const accounts = d && d.status === 'ok' ? d.accounts : [];
          if (p.multi) {
            const ids = ga4Ids(draft);
            return (
              <div key={p.key} className="mapping-field">
                <span className="small muted">
                  {p.label}: {summaryLabel(ids.length)}
                </span>
                <PropertyMultiSelect
                  accounts={accounts}
                  value={ids}
                  onChange={setGa4}
                  clientId={client.id}
                  ready={d?.status === 'ok'}
                  disabledReason={d && d.status !== 'ok' && d.status !== 'loading' ? 'Descubrimiento no disponible' : undefined}
                />
              </div>
            );
          }
          if (p.key === 'spotify_ads') {
            const sources = spotifySources(draft);
            const current = sources[0]?.ad_account_id ?? '';
            const known = accounts.some((account) => account.id === current);
            return <label key={p.key} className="mapping-field">
              <span className="small muted">Spotify Ads · fuente</span>
              <select className="input" value={current} onChange={(event) => setAccount(p, event.target.value)}>
                <option value="">No configurado</option>
                {current && !known && <option value={current}>ID {current} · fuente vinculada</option>}
                {accounts.map((account) => accountOption(account, client.id))}
              </select>
              {discovery.spotify_ads?.status === 'auth_error' && <span className="form-error small">Autenticación de Spotify no disponible.</span>}
              {discovery.spotify_ads?.status === 'provider_error' && <span className="form-error small">Fuente Spotify no disponible temporalmente.</span>}
            </label>;
          }
          if (p.key === 'pinterest_ads') {
            const sources = pinterestSources(draft);
            const current = sources[0]?.ad_account_id ?? '';
            const known = accounts.some((account) => account.id === current);
            return <label key={p.key} className="mapping-field">
              <span className="small muted">Pinterest Ads · fuente</span>
              <select className="input" value={current} onChange={(event) => setAccount(p, event.target.value)}>
                <option value="">No configurado</option>
                {current && !known && <option value={current}>ID {current} · fuente vinculada</option>}
                {accounts.map((account) => accountOption(account, client.id))}
              </select>
              {discovery.pinterest_ads?.status === 'auth_error' && <span className="form-error small">Autenticación de Pinterest no disponible.</span>}
              {discovery.pinterest_ads?.status === 'permission_error' && <span className="form-error small">Faltan permisos para listar fuentes de Pinterest.</span>}
              {discovery.pinterest_ads?.status === 'provider_error' && <span className="form-error small">Pinterest Ads no disponible temporalmente.</span>}
            </label>;
          }
          const platform = p.key as 'google_ads' | 'meta_ads';
          const current = primaryAccountId(draft, platform);
          const known = accounts.some((a) => a.id === current);
          return (
            <label key={p.key} className="mapping-field">
              <span className="small muted">{p.label} · cuenta principal</span>
              <select className="input" value={current} onChange={(e) => setAccount(p, e.target.value)}>
                <option value="">— Pendiente de asignación —</option>
                {current && !known && <option value={current}>ID {current} {d?.status === 'ok' ? '(no encontrada en el descubrimiento)' : ''}</option>}
                {accounts.map((a) => accountOption(a, client.id))}
              </select>
            </label>
          );
        })}
      </div>

      {/* Fuentes de campañas: a todo el ancho, para que las listas de campañas
          y de conjuntos de anuncios tengan sitio real. */}
      <div className="csrc-panels">
        {PLATFORMS.filter((p) => !p.multi && p.key !== 'spotify_ads' && p.key !== 'pinterest_ads').map((p) => {
          const d = discovery[p.key];
          const platform = p.key as 'google_ads' | 'meta_ads';
          return (
            <CampaignSourceEditor
              key={p.key}
              platform={platform}
              route={p.route as 'google-ads' | 'meta'}
              label={p.label}
              sources={adSourcesOf(draft, platform)}
              onChange={(next) => setSources(platform, next)}
              accounts={d && d.status === 'ok' ? d.accounts : []}
              clientId={client.id}
            />
          );
        })}
      </div>

      <div className="client-report-order">
        <label>Categoría Excel
          <select className="input" value={draft.report_category ?? 'GENERAL'} onChange={(event) => setDraft({ ...draft, report_category: event.target.value as ApiClient['report_category'] })}>
            {REPORT_CATEGORIES.map((category) => <option key={category} value={category}>{category}</option>)}
          </select>
        </label>
        <label>Posición en el Excel
          <input className="input" type="number" min="1" step="1" value={draft.report_position ?? ''} onChange={(event) => setDraft({ ...draft, report_position: event.target.value === '' ? null : Math.max(1, Math.trunc(Number(event.target.value))) })} placeholder="Al final de la categoría" />
        </label>
      </div>
      {duplicatePosition && <p className="muted small" role="status">Hay más de un cliente en la posición {draft.report_position} de {category}.</p>}

      {client.notes && <p className="client-notes small">{client.notes}</p>}
      {error && (
        <p className="form-error small" role="alert">
          {error}
        </p>
      )}

      <footer className="client-row-foot">
        {saved && <span className="saved small">✓ Guardado</span>}
        {dirty && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft(client)} disabled={saving}>
            Descartar
          </button>
        )}
        <button
          type="button"
          className="btn btn-primary btn-sm"
          disabled={!dirty || saving}
          onClick={() =>
            save({
              active: draft.active,
              // Canónico: las listas de fuentes. El backend deriva de ellas los
              // campos sueltos de "cuenta principal".
              google_ads_sources: draft.google_ads_sources ?? [],
              meta_ads_sources: draft.meta_ads_sources ?? [],
              spotify_ads_sources: spotifySources(draft),
              pinterest_ads_sources: pinterestSources(draft),
              ga4_property_ids: ga4Ids(draft),
              ga4_account_id: draft.ga4_account_id,
              report_category: draft.report_category ?? null,
              report_position: draft.report_position ?? null,
            })
          }
        >
          {saving ? 'Guardando…' : 'Guardar mapeo'}
        </button>
      </footer>

      {editing && (
        <ClientEditor
          title={`Editar ${client.name}`}
          initial={client}
          onCancel={() => setEditing(false)}
          onSave={async (data) => {
            await api.updateClient(client.id, data);
            setEditing(false);
            onSaved();
          }}
        />
      )}
    </article>
  );
}

/* ---------------------------------------------------------------- editor */

function ClientEditor({
  title,
  initial,
  onSave,
  onCancel,
}: {
  title: string;
  initial?: ApiClient;
  onSave: (data: Partial<ApiClient>) => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState({
    name: initial?.name ?? '',
    slug: initial?.slug ?? '',
    logo: initial?.logo ?? '',
    currency: initial?.currency ?? 'MXN',
    timezone: initial?.timezone ?? 'America/Mexico_City',
    brand_color: initial?.brand_color ?? '#1d4ed8',
    notes: initial?.notes ?? '',
    active: initial?.active ?? true,
  });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.type === 'checkbox' ? (e.target as HTMLInputElement).checked : e.target.value }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSave({
        name: form.name.trim(),
        ...(form.slug.trim() ? { slug: form.slug.trim() } : {}),
        logo: form.logo.trim() || null,
        currency: form.currency.trim().toUpperCase(),
        timezone: form.timezone.trim(),
        brand_color: form.brand_color,
        notes: form.notes.trim() || null,
        active: form.active,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="client-editor card" onSubmit={submit}>
      <h3>{title}</h3>
      <div className="editor-grid">
        <label>
          Nombre
          <input className="input" required value={form.name} onChange={set('name')} />
        </label>
        <label>
          Slug {initial ? '' : <span className="muted">(opcional, se genera del nombre)</span>}
          <input className="input" value={form.slug} onChange={set('slug')} pattern="[a-z0-9]+(-[a-z0-9]+)*" placeholder="mi-cliente" />
        </label>
        <label>
          Logo (URL https:// o ruta /logos/…)
          <input className="input" value={form.logo} onChange={set('logo')} placeholder="https://…" />
        </label>
        <label>
          Moneda
          <input className="input" value={form.currency} onChange={set('currency')} maxLength={3} required />
        </label>
        <label>
          Zona horaria
          <input className="input" value={form.timezone} onChange={set('timezone')} required />
        </label>
        <label>
          Color de marca
          <input className="input" type="color" value={form.brand_color} onChange={set('brand_color')} />
        </label>
        <label className="span-2">
          Notas
          <textarea className="input" rows={2} value={form.notes} onChange={set('notes')} />
        </label>
        <label className="switch">
          <input type="checkbox" checked={form.active} onChange={set('active')} />
          <span>Activo</span>
        </label>
      </div>
      {error && (
        <p className="form-error small" role="alert">
          {error}
        </p>
      )}
      <div className="drp-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          Cancelar
        </button>
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? 'Guardando…' : 'Guardar'}
        </button>
      </div>
    </form>
  );
}
