import type { WooSyncFilters, WooSyncOverview } from '../types/woosync';

export interface WooSyncState {
  status: 'idle' | 'loading' | 'ready' | 'error';
  data: WooSyncOverview | null;
  error?: string;
  /** true mientras se consulta una nueva sede/periodo conservando la vista previa. */
  refreshing: boolean;
}

export type WooSyncFetcher = (filters: WooSyncFilters, signal: AbortSignal) => Promise<WooSyncOverview>;

export const INITIAL_WOOSYNC_STATE: WooSyncState = { status: 'idle', data: null, refreshing: false };

/**
 * Serializa las consultas: solo la ÚLTIMA llamada a `load` puede modificar el estado.
 * Combina AbortController con un id de petición, de modo que una respuesta vieja no
 * sobrescriba una nueva aunque el fetcher ignore la señal de aborto.
 */
export function createWooSyncLoader(fetcher: WooSyncFetcher, onState: (state: WooSyncState) => void) {
  let seq = 0;
  let ctrl: AbortController | null = null;
  let state = INITIAL_WOOSYNC_STATE;
  const emit = (next: WooSyncState) => { state = next; onState(next); };

  return {
    async load(filters: WooSyncFilters): Promise<void> {
      ctrl?.abort();
      const id = ++seq;
      const current = new AbortController();
      ctrl = current;
      // Con datos previos no se vacía la pantalla: se atenúa mientras llega la nueva sede.
      emit(state.data ? { status: 'ready', data: state.data, refreshing: true } : { status: 'loading', data: null, refreshing: false });
      try {
        const data = await fetcher(filters, current.signal);
        if (id === seq) emit({ status: 'ready', data, refreshing: false });
      } catch (error) {
        if (id !== seq) return;
        // Un error no deja datos de otra sede junto a los filtros nuevos.
        emit({ status: 'error', data: null, error: error instanceof Error ? error.message : String(error), refreshing: false });
      }
    },
    /** Invalida cualquier consulta en vuelo (p. ej. al desmontar). */
    cancel(): void {
      seq += 1;
      ctrl?.abort();
      ctrl = null;
    },
  };
}
