import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api/backend';
import type {
  AdSetSelection,
  AdSource,
  Allocation,
  DiscoveredAccount,
  DiscoveredAdSet,
  DiscoveredCampaign,
} from '../types/dashboard';
import { isWholeAccountSource, sourceAccountId } from '../types/dashboard';

/**
 * "Fuentes de campañas" de un cliente en una plataforma de anuncios.
 *
 * Una cuenta publicitaria no equivale a un cliente: dentro de la cuenta de un
 * cliente pueden vivir campañas que pertenecen lógicamente a otro. Aquí se
 * declara esa pertenencia sin mover nada en Google Ads ni en Meta.
 *
 * La primera fuente es la CUENTA PRINCIPAL (se edita con el selector de arriba);
 * este componente gestiona las demás.
 */

type Route = 'google-ads' | 'meta';

interface Props {
  platform: 'google_ads' | 'meta_ads';
  route: Route;
  label: string;
  /** Todas las fuentes del cliente, incluida la principal en la posición 0. */
  sources: AdSource[];
  onChange: (sources: AdSource[]) => void;
  /** Cuentas descubiertas, para elegir la de origen. */
  accounts: DiscoveredAccount[];
  clientId: string;
}

type Level = 'account' | 'campaign' | 'adset';

const LEVELS: { value: Level; label: string; hint: string }[] = [
  { value: 'account', label: 'Toda la cuenta', hint: 'Todas las campañas elegibles de la cuenta.' },
  { value: 'campaign', label: 'Campañas específicas', hint: 'Solo las campañas marcadas, completas.' },
  {
    value: 'adset',
    label: 'Conjuntos de anuncios específicos',
    hint: 'Solo esos conjuntos dentro de una campaña. La campaña se contabiliza parcial: sus métricas son la suma de los conjuntos elegidos.',
  },
];

const ALLOCATIONS: { value: Allocation; label: string; hint: string }[] = [
  { value: 'exclusive', label: 'Exclusiva', hint: 'Solo este cliente las contabiliza; el dueño de la cuenta deja de verlas.' },
  { value: 'shared', label: 'Compartida', hint: 'Las contabilizan este cliente y el dueño de la cuenta.' },
];

function makeSource(
  platform: Props['platform'],
  accountId: string,
  campaignIds: string[],
  allocation: Allocation,
  selections: AdSetSelection[] = [],
): AdSource {
  return platform === 'google_ads'
    ? { customer_id: accountId, login_customer_id: null, campaign_ids: campaignIds, allocation }
    : { ad_account_id: accountId, campaign_ids: campaignIds, allocation, adset_selections: selections };
}

/** Selecciones de ad sets de una fuente (solo Meta las tiene). */
export function adsetSelections(source: AdSource): AdSetSelection[] {
  return ('adset_selections' in source ? source.adset_selections : undefined) ?? [];
}

/** Nivel al que está definida la fuente. */
export function sourceLevel(source: AdSource): 'account' | 'campaign' | 'adset' | 'mixed' {
  const sels = adsetSelections(source);
  if (sels.length > 0) return source.campaign_ids.length > 0 ? 'mixed' : 'adset';
  return source.campaign_ids.length > 0 ? 'campaign' : 'account';
}

/** Resumen legible de lo que consume una fuente, en sus tres niveles. */
export function campaignSummary(source: AdSource): string {
  const parts: string[] = [];
  const n = source.campaign_ids.length;
  if (n > 0) parts.push(n === 1 ? '1 campaña completa' : `${n} campañas completas`);
  const sels = adsetSelections(source);
  const adsets = sels.reduce((a, s) => a + s.adset_ids.length, 0);
  if (adsets > 0) {
    const campaigns = sels.length === 1 ? '1 campaña' : `${sels.length} campañas`;
    parts.push(`${adsets} ${adsets === 1 ? 'conjunto' : 'conjuntos'} de anuncios de ${campaigns}`);
  }
  return parts.length === 0 ? 'Cuenta completa' : parts.join(' · ');
}

export function CampaignSourceEditor({ platform, route, label, sources, onChange, accounts, clientId }: Props) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  // La cuenta principal es la que se consume ENTERA (sin campañas elegidas).
  // Todo lo demás son asignaciones parciales sobre cuentas de otros clientes.
  const primaryIdx = sources.findIndex(isWholeAccountSource);
  const primary = primaryIdx >= 0 ? sources[primaryIdx] : null;
  const extra = sources.filter((_, i) => i !== primaryIdx);
  const primaryId = primary ? sourceAccountId(primary) : null;
  const nameOf = useCallback(
    (id: string) => accounts.find((a) => a.id === id)?.name ?? `Cuenta ${id}`,
    [accounts],
  );

  const used = new Set(sources.map(sourceAccountId));
  const available = accounts.filter((a) => !used.has(a.id));

  const replace = (accountId: string, next: AdSource | null) => {
    const kept = sources.filter((s) => sourceAccountId(s) !== accountId);
    onChange(next ? [...kept, next] : kept);
    setEditing(null);
    setAdding(false);
  };

  return (
    <div className="csrc">
      <div className="csrc-head">
        <span className="small muted">Fuentes de campañas</span>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAdding((v) => !v)} disabled={available.length === 0}>
          {adding ? 'Cancelar' : '+ Agregar fuente'}
        </button>
      </div>

      {/* La cuenta principal se muestra como contexto, no se edita aquí. */}
      {primaryId && (
        <p className="csrc-primary small muted">
          Cuenta principal: <strong>{nameOf(primaryId)}</strong> · todas sus campañas (menos las cedidas a otros
          clientes)
        </p>
      )}

      {!primaryId && extra.length > 0 && (
        <p className="csrc-primary small muted">
          Sin cuenta propia: este cliente solo consume campañas alojadas en cuentas de otros clientes.
        </p>
      )}

      {extra.length === 0 && !adding && (
        <p className="small muted csrc-empty">
          Sin campañas de otras cuentas. Usa “Agregar fuente” si este cliente tiene campañas alojadas en la cuenta de otro.
        </p>
      )}

      <ul className="csrc-list">
        {extra.map((s) => {
          const id = sourceAccountId(s);
          return (
            <li key={id} className="csrc-item">
              {editing === id ? (
                <SourceForm
                  platform={platform}
                  route={route}
                  accountId={id}
                  accountName={nameOf(id)}
                  initial={s}
                  clientId={clientId}
                  onCancel={() => setEditing(null)}
                  onSave={(next) => replace(id, next)}
                />
              ) : (
                <div className="csrc-row">
                  <div>
                    <strong>{nameOf(id)}</strong> <span className="muted small">· {id}</span>
                    <div className="small muted">
                      {campaignSummary(s)} · {s.allocation === 'exclusive' ? 'Exclusiva' : 'Compartida'}
                    </div>
                  </div>
                  <div className="csrc-actions">
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(id)}>
                      Editar
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => replace(id, null)}>
                      Quitar
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {adding && (
        <AddSource
          platform={platform}
          route={route}
          label={label}
          available={available}
          clientId={clientId}
          onCancel={() => setAdding(false)}
          onSave={(next) => replace(sourceAccountId(next), next)}
        />
      )}
    </div>
  );
}

/* --------------------------------------------------------- alta de fuente */

function AddSource({
  platform,
  route,
  label,
  available,
  clientId,
  onCancel,
  onSave,
}: {
  platform: Props['platform'];
  route: Route;
  label: string;
  available: DiscoveredAccount[];
  clientId: string;
  onCancel: () => void;
  onSave: (s: AdSource) => void;
}) {
  const [accountId, setAccountId] = useState('');
  const account = available.find((a) => a.id === accountId);
  return (
    <div className="csrc-form card">
      <label className="csrc-field">
        <span className="small muted">Paso 1 · Cuenta de origen ({label})</span>
        <select className="input" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          <option value="">— Elige una cuenta —</option>
          {available.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name ?? '(sin nombre)'} · {a.id}
            </option>
          ))}
        </select>
      </label>
      {accountId ? (
        <SourceForm
          platform={platform}
          route={route}
          accountId={accountId}
          accountName={account?.name ?? `Cuenta ${accountId}`}
          clientId={clientId}
          onCancel={onCancel}
          onSave={onSave}
        />
      ) : (
        <div className="drp-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            Cancelar
          </button>
        </div>
      )}
    </div>
  );
}

/** Conjuntos de anuncios de UNA campaña, cargados de la API al desplegarla. */
function AdSetPicker({
  accountId,
  campaignId,
  clientId,
  allocation,
  selected,
  onChange,
}: {
  accountId: string;
  campaignId: string;
  clientId: string;
  allocation: Allocation;
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [adsets, setAdSets] = useState<DiscoveredAdSet[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setAdSets(null);
    setError(null);
    api
      .adsets(accountId, campaignId)
      .then((res) => {
        if (!alive) return;
        if (res.status !== 'ok') {
          setError(res.detail ?? res.message ?? 'No se pudieron cargar los conjuntos de anuncios.');
          setAdSets([]);
          return;
        }
        setAdSets(res.adsets);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        setAdSets([]);
      });
    return () => {
      alive = false;
    };
  }, [accountId, campaignId]);

  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  if (adsets === null) return <p className="small muted csrc-empty">Cargando conjuntos de anuncios…</p>;
  if (error)
    return (
      <p className="form-error small" role="alert">
        {error}
      </p>
    );
  if (adsets.length === 0) return <p className="small muted csrc-empty">Esta campaña no tiene conjuntos de anuncios.</p>;

  return (
    <div className="csrc-adsets" role="group" aria-label="Conjuntos de anuncios">
      {adsets.map((a) => {
        // Un conjunto ya exclusivo de otro cliente no se puede tomar en exclusiva.
        const taken = a.assigned_to && a.assigned_to.id !== clientId && a.assigned_to.allocation === 'exclusive';
        const checked = selected.includes(a.adset_id);
        return (
          <label key={a.adset_id} className={`csrc-row-item${taken && !checked ? ' csrc-taken' : ''}`}>
            <input
              type="checkbox"
              checked={checked}
              disabled={Boolean(taken) && !checked && allocation === 'exclusive'}
              onChange={() => toggle(a.adset_id)}
            />
            <span className="csrc-item-name">{a.adset_name ?? '(sin nombre)'}</span>
            <span className="csrc-item-meta muted small">
              {a.effective_status ?? a.status}
              <span className="csrc-item-id">ID {a.adset_id}</span>
            </span>
            <span className="csrc-item-sub muted small">
              {a.campaign_name ?? ''}
              {taken ? ` · exclusivo de ${a.assigned_to!.name}` : ''}
            </span>
          </label>
        );
      })}
    </div>
  );
}

/* ------------------------------------------- selección de campañas + modo */

export function SourceForm({
  platform,
  route,
  accountId,
  accountName,
  initial,
  clientId,
  onCancel,
  onSave,
}: {
  platform: Props['platform'];
  route: Route;
  accountId: string;
  accountName: string;
  initial?: AdSource;
  clientId: string;
  onCancel: () => void;
  onSave: (s: AdSource) => void;
}) {
  const [campaigns, setCampaigns] = useState<DiscoveredCampaign[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string[]>(initial?.campaign_ids ?? []);
  const [allocation, setAllocation] = useState<Allocation>(initial?.allocation ?? 'exclusive');
  // Meta admite tres niveles; Google Ads solo cuenta o campañas.
  // 'mixed' (campañas enteras + ad sets) se edita desde el nivel de ad sets:
  // es el caso raro y así el formulario no necesita un cuarto modo.
  const [level, setLevel] = useState<Level>(() => {
    const l = initial ? sourceLevel(initial) : 'account';
    return l === 'mixed' ? 'adset' : l;
  });
  const [selections, setSelections] = useState<AdSetSelection[]>(initial ? adsetSelections(initial) : []);
  const [openCampaign, setOpenCampaign] = useState<string | null>(null);
  const supportsAdSets = platform === 'meta_ads';

  // Paso 2: las campañas se cargan de la cuenta elegida, nunca se inventan.
  useEffect(() => {
    let alive = true;
    setCampaigns(null);
    setError(null);
    api
      .campaigns(route, accountId)
      .then((res) => {
        if (!alive) return;
        if (res.status !== 'ok') {
          setError(res.detail ?? res.message ?? 'No se pudieron cargar las campañas.');
          setCampaigns([]);
          return;
        }
        setCampaigns(res.campaigns);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        setCampaigns([]);
      });
    return () => {
      alive = false;
    };
  }, [route, accountId]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!campaigns) return [];
    if (!q) return campaigns;
    return campaigns.filter((c) => c.campaign_name.toLowerCase().includes(q) || c.campaign_id.includes(q));
  }, [campaigns, query]);

  const toggle = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  // Una campaña que ya es exclusiva de OTRO cliente no se puede tomar en exclusiva.
  const conflict = (c: DiscoveredCampaign) =>
    c.assigned_to && c.assigned_to.id !== clientId && c.assigned_to.allocation === 'exclusive';

  const blocked = allocation === 'exclusive' && (campaigns ?? []).some((c) => selected.includes(c.campaign_id) && conflict(c));
  const invalidSelection =
    (level === 'campaign' && selected.length === 0) ||
    (level === 'adset' && selections.every((selection) => selection.adset_ids.length === 0));

  const changeAllocation = (next: Allocation) => {
    setAllocation(next);
    // La UI ofrece una sola asignación para la fuente; mantenemos el mismo
    // valor en cada selección de ad sets para que editar Exclusiva/Compartida
    // no deje datos internos con la opción anterior.
    setSelections((current) => current.map((selection) => ({ ...selection, allocation: next })));
  };

  return (
    <div className="csrc-picker">
      <fieldset className="csrc-alloc">
        <legend className="small muted">Paso 2 · Qué consume de {accountName}</legend>
        {LEVELS.filter((l) => supportsAdSets || l.value !== 'adset').map((l) => (
          <label key={l.value} className="radio" title={l.hint}>
            <input
              type="radio"
              name={`level-${accountId}`}
              checked={level === l.value}
              onChange={() => setLevel(l.value)}
            />
            <span>{l.label}</span>
          </label>
        ))}
        <p className="small muted">{LEVELS.find((l) => l.value === level)!.hint}</p>
      </fieldset>

      {level === 'account' ? null : (
      <>
      <p className="small muted">
        Paso 3 · {level === 'adset' ? 'Campaña y sus conjuntos de anuncios' : 'Campañas'} de{' '}
        <strong>{accountName}</strong>
      </p>
      <input
        type="search"
        className="input search"
        placeholder="Buscar campaña…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        aria-label="Buscar campaña"
      />

      {campaigns === null && <p className="small muted csrc-empty">Cargando campañas…</p>}
      {error && (
        <p className="form-error small" role="alert">
          {error}
        </p>
      )}
      {campaigns !== null && campaigns.length > 0 && visible.length === 0 && (
        <p className="small muted csrc-empty">Ninguna campaña coincide con la búsqueda.</p>
      )}

      <div className="csrc-campaigns" role="group" aria-label="Campañas">
        {visible.map((c) => {
          const taken = conflict(c);
          if (level === 'adset') {
            const sel = selections.find((x) => x.campaign_id === c.campaign_id);
            const open = openCampaign === c.campaign_id;
            return (
              <div key={c.campaign_id} className="csrc-campaign-group">
                <button
                  type="button"
                  className="csrc-row-item csrc-campaign-toggle"
                  onClick={() => setOpenCampaign(open ? null : c.campaign_id)}
                  aria-expanded={open}
                >
                  <span className="csrc-caret" aria-hidden>
                    {open ? '▾' : '▸'}
                  </span>
                  <span className="csrc-item-name">{c.campaign_name}</span>
                  <span className="csrc-item-meta muted small">
                    {c.status}
                    <span className="csrc-item-id">ID {c.campaign_id}</span>
                  </span>
                  <span className="csrc-item-sub muted small">
                    {sel && sel.adset_ids.length > 0
                      ? `${sel.adset_ids.length} ${sel.adset_ids.length === 1 ? 'conjunto seleccionado' : 'conjuntos seleccionados'}`
                      : 'Sin conjuntos seleccionados'}
                  </span>
                </button>
                {open && (
                  <AdSetPicker
                    accountId={accountId}
                    campaignId={c.campaign_id}
                    clientId={clientId}
                    allocation={allocation}
                    selected={sel?.adset_ids ?? []}
                    onChange={(ids) =>
                      setSelections((prev) => {
                        const rest = prev.filter((x) => x.campaign_id !== c.campaign_id);
                        return ids.length > 0
                          ? [...rest, { campaign_id: c.campaign_id, adset_ids: ids, allocation }]
                          : rest;
                      })
                    }
                  />
                )}
              </div>
            );
          }
          return (
            <label key={c.campaign_id} className={`csrc-row-item${taken ? ' csrc-taken' : ''}`}>
              <input
                type="checkbox"
                checked={selected.includes(c.campaign_id)}
                onChange={() => toggle(c.campaign_id)}
              />
              <span className="csrc-item-name">{c.campaign_name}</span>
              <span className="csrc-item-meta muted small">
                {c.status}
                <span className="csrc-item-id">ID {c.campaign_id}</span>
              </span>
              <span className="csrc-item-sub muted small">
                {c.account_name ?? accountName}
                {taken ? ` · exclusiva de ${c.assigned_to!.name}` : ''}
              </span>
            </label>
          );
        })}
      </div>
      </>
      )}

      <fieldset className="csrc-alloc">
        <legend className="small muted">Paso 3 · Asignación</legend>
        {ALLOCATIONS.map((a) => (
          <label key={a.value} className="radio" title={a.hint}>
            <input
              type="radio"
              name={`alloc-${accountId}`}
              checked={allocation === a.value}
              onChange={() => changeAllocation(a.value)}
            />
            <span>{a.label}</span>
          </label>
        ))}
        <p className="small muted">{ALLOCATIONS.find((a) => a.value === allocation)!.hint}</p>
      </fieldset>

      <p className="small muted">
        {level === 'account'
          ? 'Se usarán todas las campañas elegibles de esta cuenta (menos las cedidas a otros clientes).'
          : level === 'campaign'
            ? selected.length === 0
              ? 'Marca al menos una campaña.'
              : `${selected.length} campaña${selected.length === 1 ? '' : 's'} completa${selected.length === 1 ? '' : 's'}.`
            : selections.length === 0
              ? 'Abre una campaña y marca sus conjuntos de anuncios.'
              : campaignSummary(makeSource(platform, accountId, [], allocation, selections))}
      </p>
      {blocked && (
        <p className="form-error small" role="alert">
          Alguna de las campañas marcadas ya es exclusiva de otro cliente. Usa “Compartida” o quítasela primero.
        </p>
      )}

      <div className="drp-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          Cancelar
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={blocked || invalidSelection}
          onClick={() =>
            onSave(
              makeSource(
                platform,
                accountId,
                level === 'campaign' ? selected : [],
                allocation,
                level === 'adset' ? selections.filter((x) => x.adset_ids.length > 0) : [],
              ),
            )
          }
        >
          Guardar fuente
        </button>
      </div>
    </div>
  );
}
