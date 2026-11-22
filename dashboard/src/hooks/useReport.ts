import { useCallback, useEffect, useState } from 'react';
import type { ClientConfig } from '../config/clients';
import { fetchPlatformReport } from '../services/reportService';
import type { Platform } from '../types/metrics';
import type { DateSelection, PlatformReport } from '../types/report';

export type ReportState =
  | { status: 'loading'; data: PlatformReport | null }
  | { status: 'error'; error: string }
  | { status: 'empty' }
  | { status: 'ready'; data: PlatformReport };

export function useReport(client: ClientConfig, platform: Platform, selection: DateSelection, today: string) {
  const [state, setState] = useState<ReportState>({ status: 'loading', data: null });
  const [nonce, setNonce] = useState(0);
  const retry = useCallback(() => setNonce((n) => n + 1), []);

  const { range, compareRange } = selection;
  useEffect(() => {
    const ctrl = new AbortController();
    setState((s) => ({ status: 'loading', data: s.status === 'ready' ? s.data : null }));
    fetchPlatformReport(client, platform, range, compareRange, today, ctrl.signal)
      .then((data) => {
        if (ctrl.signal.aborted) return;
        if (!data || (data.current.campaigns.length === 0 && (!data.previous || data.previous.campaigns.length === 0))) {
          setState({ status: 'empty' });
        } else {
          setState({ status: 'ready', data });
        }
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        setState({ status: 'error', error: err instanceof Error ? err.message : String(err) });
      });
    return () => ctrl.abort();
  }, [client, platform, range.start, range.end, compareRange?.start, compareRange?.end, today, nonce]);

  return { state, retry };
}
