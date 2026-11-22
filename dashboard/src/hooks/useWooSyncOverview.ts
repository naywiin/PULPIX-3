import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/backend';
import { createWooSyncLoader, INITIAL_WOOSYNC_STATE, type WooSyncState } from '../lib/woosyncLoader';
import type { WooSyncFilters } from '../types/woosync';

export type { WooSyncState };

/** Consulta automática cada vez que cambian los filtros aplicados (o `requestVersion`). */
export function useWooSyncOverview(enabled: boolean, filters: WooSyncFilters, requestVersion = 0) {
  const [state, setState] = useState<WooSyncState>(INITIAL_WOOSYNC_STATE);
  const loaderRef = useRef<ReturnType<typeof createWooSyncLoader> | null>(null);
  if (!loaderRef.current) loaderRef.current = createWooSyncLoader(api.woosyncOverview, setState);
  const branchKey = filters.branches.join(',');

  const load = useCallback(() => {
    if (enabled) void loaderRef.current!.load(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, filters.from, filters.to, branchKey, filters.compareTo, requestVersion]);

  useEffect(() => {
    load();
    return () => loaderRef.current!.cancel();
  }, [load]);

  return { ...state, retry: load };
}
