import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/backend';
import type { ClientConfig } from '../config/clients';
import { normalizeDashboard } from '../lib/dashboard';
import { buildMockDashboard } from '../services/mockDashboard';
import type { DashboardData, DataSource } from '../types/dashboard';
import type { DateSelection } from '../types/report';

/** Coincide con el TTL de cache del backend: refrescar más seguido no aporta datos nuevos. */
export const AUTO_REFRESH_MS = 5 * 60 * 1000;

export interface DashboardState {
  status: 'idle' | 'loading' | 'ready' | 'error';
  data: DashboardData | null;
  error?: string;
  lastUpdated: Date | null;
  /** true mientras se recarga manteniendo los datos anteriores en pantalla. */
  refreshing: boolean;
}

export function useDashboard(source: DataSource | null, client: ClientConfig | null, selection: DateSelection, today: string) {
  const [state, setState] = useState<DashboardState>({ status: 'idle', data: null, lastUpdated: null, refreshing: false });
  const ctrlRef = useRef<AbortController | null>(null);
  const lastRef = useRef<number>(0);
  const { range, compare, compareRange } = selection;

  const load = useCallback(
    async (opts: { force?: boolean; keepData?: boolean } = {}) => {
      if (!source || !client) return;
      ctrlRef.current?.abort();
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      setState((s) => ({
        ...s,
        status: opts.keepData && s.data ? s.status : 'loading',
        data: opts.keepData ? s.data : null,
        refreshing: Boolean(opts.keepData && s.data),
        error: undefined,
      }));
      try {
        const data =
          source === 'real'
            ? normalizeDashboard(await api.dashboard(client.id, { range, compare, compareRange, refresh: opts.force }, ctrl.signal))
            : await buildMockDashboard(client, { preset: 'custom', range, compare, compareRange }, today, ctrl.signal);
        if (ctrl.signal.aborted) return;
        // Salvaguarda: nunca mostrar datos de otro cliente.
        if (data.clientId !== client.id) throw new Error('La respuesta no corresponde al cliente seleccionado.');
        lastRef.current = Date.now();
        setState({ status: 'ready', data, lastUpdated: new Date(), refreshing: false });
      } catch (e) {
        if (ctrl.signal.aborted) return;
        const msg = e instanceof Error ? e.message : String(e);
        setState((s) => ({ ...s, status: s.data && opts.keepData ? 'ready' : 'error', error: msg, refreshing: false }));
      }
    },
    // Dependencias primitivas: el objeto selection cambia de identidad en cada render.
    [source, client, range.start, range.end, compare, compareRange?.start, compareRange?.end, today],
  );

  // Carga inicial y al cambiar cliente/periodo: los datos previos se descartan (no mezclar clientes).
  useEffect(() => {
    load();
    return () => ctrlRef.current?.abort();
  }, [load]);

  // Auto-refresh cada 5 min, solo con la pestaña visible (sin polling agresivo).
  useEffect(() => {
    if (!source || !client) return;
    const tick = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastRef.current >= AUTO_REFRESH_MS - 1000) load({ keepData: true });
    };
    const id = window.setInterval(tick, AUTO_REFRESH_MS);
    document.addEventListener('visibilitychange', tick);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [load, source, client]);

  /** Botón "Actualizar": pide al backend saltarse la cache. */
  const refresh = useCallback(() => load({ force: true, keepData: true }), [load]);
  const retry = useCallback(() => load(), [load]);

  return { ...state, refresh, retry };
}
