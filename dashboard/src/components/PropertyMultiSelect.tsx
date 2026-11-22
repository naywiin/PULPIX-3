import { useEffect, useMemo, useRef, useState } from 'react';
import type { DiscoveredAccount } from '../types/dashboard';

/**
 * Selector múltiple de propiedades GA4 para un cliente.
 *
 * - checkboxes: se marcan y desmarcan sin cerrar el panel
 * - buscador por nombre (también por ID y por nombre de cuenta)
 * - agrupado por cuenta de Analytics, mostrando cuenta, propiedad, ID y moneda
 * - cerrado, resume "N propiedades seleccionadas"
 *
 * Una propiedad ya asignada a OTRO cliente se muestra deshabilitada: una
 * propiedad pertenece a un solo cliente (nunca se mezclan datos).
 */

interface Props {
  accounts: DiscoveredAccount[];
  value: string[];
  onChange: (ids: string[]) => void;
  /** Para saber qué asignaciones ajenas bloquear. */
  clientId: string;
  /** false mientras el descubrimiento no ha respondido. */
  ready: boolean;
  disabledReason?: string;
}

export function summaryLabel(count: number): string {
  if (count === 0) return '— Pendiente de asignación —';
  if (count === 1) return '1 propiedad seleccionada';
  return `${count} propiedades seleccionadas`;
}

/** Agrupa por cuenta contenedora, en orden alfabético y propiedades por nombre. */
function byAccount(accounts: DiscoveredAccount[]): { account: string; items: DiscoveredAccount[] }[] {
  const groups = new Map<string, DiscoveredAccount[]>();
  for (const a of accounts) {
    const key = a.account_name ?? 'Sin cuenta';
    const list = groups.get(key);
    if (list) list.push(a);
    else groups.set(key, [a]);
  }
  return [...groups.entries()]
    .sort((x, y) => x[0].localeCompare(y[0], 'es'))
    .map(([account, items]) => ({
      account,
      items: [...items].sort((a, b) => (a.name ?? '').localeCompare(b.name ?? '', 'es')),
    }));
}

function matches(a: DiscoveredAccount, q: string): boolean {
  if (!q) return true;
  return [a.name, a.id, a.account_name, a.currency].some((v) => (v ?? '').toLowerCase().includes(q));
}

export function PropertyMultiSelect({ accounts, value, onChange, clientId, ready, disabledReason }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const boxRef = useRef<HTMLDivElement>(null);

  // Cerrar al hacer clic fuera o con Escape, como el resto de popovers.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const selected = useMemo(() => new Set(value), [value]);
  const q = query.trim().toLowerCase();
  const groups = useMemo(() => byAccount(accounts.filter((a) => matches(a, q))), [accounts, q]);

  // IDs guardados que el descubrimiento no devuelve (sin permiso, o borrada en GA4):
  // se siguen mostrando para poder quitarlos, nunca se pierden en silencio.
  const unknown = value.filter((id) => !accounts.some((a) => a.id === id));

  const toggle = (id: string) => {
    if (selected.has(id)) onChange(value.filter((v) => v !== id));
    else onChange([...value, id]);
  };

  return (
    <div className="pms" ref={boxRef}>
      <button
        type="button"
        className="input pms-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={Boolean(disabledReason)}
        title={disabledReason}
      >
        <span className={value.length ? undefined : 'muted'}>{disabledReason ?? summaryLabel(value.length)}</span>
        <span className="caret" aria-hidden>
          ▾
        </span>
      </button>

      {open && (
        <div className="pms-pop card" role="dialog" aria-label="Seleccionar propiedades de Google Analytics">
          <div className="pms-head">
            <input
              type="search"
              className="input search"
              placeholder="Buscar propiedad…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Buscar propiedad"
              autoFocus
            />
            {value.length > 0 && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange([])}>
                Quitar todas
              </button>
            )}
          </div>

          <div className="pms-list" role="listbox" aria-multiselectable>
            {unknown.length > 0 && (
              <div className="pms-group">
                <p className="pms-account">No encontradas en el descubrimiento</p>
                {unknown.map((id) => (
                  <label key={id} className="pms-item">
                    <input type="checkbox" checked onChange={() => toggle(id)} />
                    <span className="pms-name">ID {id}</span>
                    <span className="muted small">sin acceso o fuera de la cuenta</span>
                  </label>
                ))}
              </div>
            )}

            {!ready && <p className="muted small pms-empty">Consultando propiedades…</p>}
            {ready && groups.length === 0 && unknown.length === 0 && (
              <p className="muted small pms-empty">{q ? 'Ninguna propiedad coincide con la búsqueda.' : 'No hay propiedades disponibles.'}</p>
            )}

            {groups.map(({ account, items }) => (
              <div key={account} className="pms-group">
                <p className="pms-account">{account}</p>
                {items.map((a) => {
                  const taken = a.assigned_to && a.assigned_to.id !== clientId;
                  const checked = selected.has(a.id);
                  return (
                    <label key={a.id} className={`pms-item${taken && !checked ? ' pms-taken' : ''}`}>
                      <input type="checkbox" checked={checked} disabled={Boolean(taken) && !checked} onChange={() => toggle(a.id)} />
                      <span className="pms-name">{a.name ?? '(sin nombre)'}</span>
                      <span className="muted small pms-meta">
                        {a.id}
                        {a.currency ? ` · ${a.currency}` : ''}
                        {a.status && a.status !== 'ACTIVE' ? ` · ${a.status}` : ''}
                        {taken && !checked ? ` · asignada a ${a.assigned_to!.name}` : ''}
                      </span>
                    </label>
                  );
                })}
              </div>
            ))}
          </div>

          <div className="pms-foot">
            <span className="muted small">{summaryLabel(value.length)}</span>
            <button type="button" className="btn btn-sm" onClick={() => setOpen(false)}>
              Listo
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
