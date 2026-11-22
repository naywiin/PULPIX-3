import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/backend';
import { CLIENTS, type ClientConfig } from '../config/clients';
import { withoutExcluded } from '../config/exclusions';
import { clientConfigFromApi } from '../lib/dashboard';
import type { ApiClient, DataSource } from '../types/dashboard';

export interface ClientsState {
  status: 'loading' | 'ready' | 'error';
  /** Clientes activos para el selector del dashboard. */
  clients: ClientConfig[];
  /** Registro completo (solo backend) para Settings → Clients. */
  registry: ApiClient[];
  error?: string;
}

/** Clientes del registro central (backend) o, en modo mock, del config local. */
export function useClients(source: DataSource | null) {
  const [state, setState] = useState<ClientsState>({ status: 'loading', clients: [], registry: [] });
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!source) return;
    if (source === 'mock') {
      setState({ status: 'ready', clients: withoutExcluded(CLIENTS, (c) => [c.name, c.id]), registry: [] });
      return;
    }
    const ctrl = new AbortController();
    api
      .listClients(ctrl.signal)
      .then((all) => {
        if (ctrl.signal.aborted) return;
        // Segunda barrera: el backend ya excluye, aquí tampoco entra al selector.
        const registry = withoutExcluded(all, (c) => [c.name, c.slug, c.id]);
        setState({ status: 'ready', registry, clients: registry.filter((c) => c.active).map(clientConfigFromApi) });
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        setState((s) => ({ ...s, status: 'error', error: e instanceof Error ? e.message : String(e) }));
      });
    return () => ctrl.abort();
  }, [source, nonce]);

  return { ...state, reload };
}
