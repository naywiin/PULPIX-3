import type { AdSetCatalog, ApiClient, CampaignCatalog, DiscoveryResult } from '../types/dashboard';
import type { ComparePreset, DateRange } from '../types/report';
import type { WooSyncFilters, WooSyncOverview } from '../types/woosync';
import type { BudgetPlatform, DownloadedReport, MonthlyBudget, MonthlyCampaignAssignment, PerformanceDetailLevel, PerformanceDetailPlatform, PerformanceDetailResponse, PerformanceDetailSpend, PerformanceDetailStatus, PerformanceSummary, ReportingGroup, ReportingGroupSnapshotResult } from '../types/reporting';
import { buildWooSyncQuery } from '../lib/woosync';

/**
 * Cliente HTTP del backend (FastAPI). El navegador solo habla con /api:
 * las credenciales de Google/Meta viven exclusivamente en el servidor.
 */
const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? '';

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), init?.timeoutMs ?? 120_000);
  const onAbort = () => ctrl.abort();
  init?.signal?.addEventListener('abort', onAbort);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}) },
    });
    const text = await res.text();
    const body = text ? safeJson(text) : null;
    if (!res.ok) {
      const detail = body && typeof body === 'object' && 'detail' in body ? formatDetail((body as { detail: unknown }).detail) : res.statusText;
      throw new ApiError(detail || `HTTP ${res.status}`, res.status);
    }
    return body as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (init?.signal?.aborted) throw e;
    throw new ApiError(ctrl.signal.aborted ? 'El backend tardó demasiado en responder.' : 'No se pudo conectar con el backend.', 0);
  } finally {
    clearTimeout(timer);
    init?.signal?.removeEventListener('abort', onAbort);
  }
}

async function requestDownload(path: string, fallbackFilename: string): Promise<DownloadedReport> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 180_000);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      signal: ctrl.signal,
      cache: 'no-store',
      headers: { Accept: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
    });
    if (!res.ok) {
      const text = await res.text();
      const body = text ? safeJson(text) : null;
      const detail = body && typeof body === 'object' && 'detail' in body ? formatDetail((body as { detail: unknown }).detail) : res.statusText;
      throw new ApiError(detail || `HTTP ${res.status}`, res.status);
    }
    const disposition = res.headers.get('Content-Disposition') ?? '';
    const match = /filename="?([^";]+)"?/i.exec(disposition);
    return { blob: await res.blob(), filename: match?.[1] ?? fallbackFilename };
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(ctrl.signal.aborted ? 'El reporte tardó demasiado en generarse.' : 'No se pudo generar el reporte.', 0);
  } finally {
    clearTimeout(timer);
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? String(d.msg) : String(d))).join('; ');
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message);
  return String(detail);
}

export interface HealthResponse {
  status: string;
  today: string;
  credentials: Record<string, string>;
}

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>('/api/health', { signal, timeoutMs: 4000 }),

  listClients: (signal?: AbortSignal) => request<{ clients: ApiClient[] }>('/api/clients', { signal }).then((r) => r.clients),
  createClient: (data: Partial<ApiClient> & { name: string }) =>
    request<ApiClient>('/api/clients', { method: 'POST', body: JSON.stringify(data) }),
  updateClient: (id: string, data: Partial<ApiClient>) =>
    request<ApiClient>(`/api/clients/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(data) }),

  discover: (platform: 'google-ads' | 'meta' | 'spotify-ads' | 'pinterest-ads' | 'ga4', refresh = false) =>
    request<DiscoveryResult>(`/api/accounts/${platform}${refresh ? '?refresh=true' : ''}`, { timeoutMs: 180_000 }),

  /** Conjuntos de anuncios reales de una campaña de Meta. */
  adsets: (accountId: string, campaignId: string, refresh = false) =>
    request<AdSetCatalog>(
      `/api/accounts/meta/${encodeURIComponent(accountId)}/campaigns/${encodeURIComponent(campaignId)}/adsets${refresh ? '?refresh=true' : ''}`,
      { timeoutMs: 180_000 },
    ),

  /** Campañas reales de una cuenta, para asignarlas a un cliente. */
  campaigns: (platform: 'google-ads' | 'meta' | 'spotify-ads' | 'pinterest-ads', accountId: string, refresh = false) =>
    request<CampaignCatalog>(
      `/api/accounts/${platform}/${encodeURIComponent(accountId)}/campaigns${refresh ? '?refresh=true' : ''}`,
      { timeoutMs: 180_000 },
    ),

  dashboard: (
    clientId: string,
    params: { range: DateRange; compare: ComparePreset; compareRange: DateRange | null; refresh?: boolean },
    signal?: AbortSignal,
  ) => {
    const q = new URLSearchParams({
      start_date: params.range.start,
      end_date: params.range.end,
      comparison: params.compare,
    });
    if (params.compare === 'custom' && params.compareRange) {
      q.set('compare_start_date', params.compareRange.start);
      q.set('compare_end_date', params.compareRange.end);
    }
    if (params.refresh) q.set('refresh', 'true');
    return request<RawDashboard>(`/api/dashboard/${encodeURIComponent(clientId)}?${q}`, { signal });
  },

  woosyncOverview: (filters: WooSyncFilters, signal?: AbortSignal) =>
    request<WooSyncOverview>(`/api/woosync/overview?${buildWooSyncQuery(filters)}`, {
      signal,
      timeoutMs: 60_000,
      cache: 'no-store',
    }),

  listBudgets: (clientId?: string, signal?: AbortSignal) => {
    const query = clientId ? `?client_id=${encodeURIComponent(clientId)}` : '';
    return request<{ budgets: MonthlyBudget[] }>(`/api/budgets${query}`, { signal }).then((result) => result.budgets);
  },

  saveBudget: (clientId: string, platform: BudgetPlatform, year: number, month: number, budget: number, reportingGroupId: string | null = null, campaign?: { account_id: string; campaign_id: string }) => {
    const params = new URLSearchParams({ ...(reportingGroupId ? { reporting_group_id: reportingGroupId } : {}), ...(campaign ?? {}) });
    return request<MonthlyBudget>(
      `/api/budgets/${encodeURIComponent(clientId)}/${platform}/${year}/${month}${params.toString() ? `?${params.toString()}` : ''}`,
      { method: 'PUT', body: JSON.stringify({ budget }) },
    );
  },

  deleteBudget: (clientId: string, platform: BudgetPlatform, year: number, month: number, reportingGroupId: string | null = null, campaign?: { account_id: string; campaign_id: string }) => {
    const params = new URLSearchParams({ ...(reportingGroupId ? { reporting_group_id: reportingGroupId } : {}), ...(campaign ?? {}) });
    const query = params.toString() ? `?${params.toString()}` : '';
    return request<{ deleted: boolean }>(`/api/budgets/${encodeURIComponent(clientId)}/${platform}/${year}/${month}${query}`, { method: 'DELETE' });
  },

  listReportingGroups: (clientId: string, signal?: AbortSignal) =>
    request<{ client_id: string; groups: ReportingGroup[] }>(`/api/reporting-groups/${encodeURIComponent(clientId)}`, { signal }),

  saveReportingGroups: (clientId: string, groups: ReportingGroup[]) =>
    request<{ client_id: string; groups: ReportingGroup[] }>(`/api/reporting-groups/${encodeURIComponent(clientId)}`, {
      method: 'PUT', body: JSON.stringify({ groups }),
    }),

  reportingGroupSnapshot: (clientId: string, year: number, month: number, signal?: AbortSignal) =>
    request<ReportingGroupSnapshotResult>(`/api/reporting-group-snapshots/${encodeURIComponent(clientId)}/${year}/${month}`, { signal, cache: 'no-store' }),

  saveReportingGroupSnapshot: (
    clientId: string, year: number, month: number, groups: ReportingGroup[], assignments: MonthlyCampaignAssignment[],
  ) => request<ReportingGroupSnapshotResult>(
    `/api/reporting-group-snapshots/${encodeURIComponent(clientId)}/${year}/${month}`,
    { method: 'PUT', body: JSON.stringify({
      groups: groups.map(({ id, name, type, active, enabled_platforms }) => ({ id, name, type, active, enabled_platforms })), assignments,
    }) },
  ),

  copyPreviousReportingGroupSnapshot: (clientId: string, year: number, month: number) =>
    request<ReportingGroupSnapshotResult>(`/api/reporting-group-snapshots/${encodeURIComponent(clientId)}/${year}/${month}/copy-previous`, { method: 'POST' }),

  initializeReportingGroupSnapshotFromCurrent: (clientId: string, year: number, month: number) =>
    request<ReportingGroupSnapshotResult>(`/api/reporting-group-snapshots/${encodeURIComponent(clientId)}/${year}/${month}/initialize-from-current`, { method: 'POST' }),

  performanceSummary: (clientId: string, range: DateRange, refresh: boolean, signal?: AbortSignal) => {
    const query = new URLSearchParams({
      client_id: clientId,
      start_date: range.start,
      end_date: range.end,
    });
    if (refresh) query.set('refresh', 'true');
    return request<PerformanceSummary>(`/api/reporting/summary?${query}`, { signal, cache: 'no-store' });
  },

  performanceDetail: (
    clientId: string,
    platform: PerformanceDetailPlatform,
    from: string,
    to: string,
    filters: { level: PerformanceDetailLevel; status: PerformanceDetailStatus; spend: PerformanceDetailSpend },
    signal?: AbortSignal,
    refresh = false,
  ) => {
    const query = new URLSearchParams({
      client_id: clientId,
      platform,
      start_date: from,
      end_date: to,
      level: filters.level,
      status: filters.status,
      spend: filters.spend,
    });
    if (refresh) query.set('refresh', 'true');
    return request<PerformanceDetailResponse>(`/api/performance/detail?${query}`, { signal, timeoutMs: 180_000 });
  },

  exportPerformanceReport: (from: string, to: string) => {
    const query = new URLSearchParams({ start_date: from, end_date: to });
    return requestDownload(
      `/api/reports/performance.xlsx?${query}`,
      `Reporte_Performance_${from}_al_${to}.xlsx`,
    );
  },
};

/** Respuesta cruda del backend; se normaliza en lib/dashboard.ts. */
export interface RawBlock {
  platform: string;
  label: string;
  status: 'ok' | 'partial' | 'not_linked' | 'not_configured' | 'auth_error' | 'permission_error' | 'provider_error' | 'error';
  source: 'real';
  account_id: string | null;
  currency?: string;
  message?: string;
  detail?: string;
  fetched_at?: string;
  current?: Record<string, unknown> | null;
  previous?: Record<string, unknown> | null;
  /** GA4 multi-propiedad. */
  property_ids?: string[];
  properties?: Record<string, unknown>[];
  warnings?: Record<string, unknown>[];
  mixed_currency?: boolean;
  /** Google Ads / Meta multi-cuenta. */
  sources?: Record<string, unknown>[];
}

export interface RawDashboard {
  client: ApiClient;
  source: 'real';
  range: DateRange;
  compare_range: DateRange | null;
  generated_at: string;
  platforms: { google_ads: RawBlock; meta_ads: RawBlock; spotify_ads?: RawBlock; pinterest_ads?: RawBlock; ga4: RawBlock };
}
