import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/backend';
import { BUDGETS_UPDATED_EVENT, BUDGET_PLATFORM_LABELS, MONTH_LABELS } from '../lib/reporting';
import type { ApiClient } from '../types/dashboard';
import type { BudgetPlatform, CampaignReportingPlatform, MonthlyBudget, MonthlyCampaignAssignment, ReportingGroup, ReportingPlatform, ReportingGroupSnapshotResult } from '../types/reporting';
import { campaignAssignmentKey, completeCampaignAssignments } from '../lib/budgetAssignments';
import { REPORTING_PLATFORMS, resolvedGroupPlatforms, setGroupPlatformEnabled } from '../lib/reportingGroupPlatforms';

interface Props { client: ApiClient; range: { start: string; end: string }; filters?: { platform: 'all' | CampaignReportingPlatform; visibleCampaignKeys?: string[] } }
interface CampaignChoice { key: string; platform: CampaignReportingPlatform; account_id: string; campaign_id: string; name: string; objective?: string; status: string }
const platformApi = { google_ads: 'google-ads', meta_ads: 'meta', spotify_ads: 'spotify-ads', pinterest_ads: 'pinterest-ads' } as const;
const PLATFORM_NAMES: Record<ReportingPlatform, string> = { google_ads: 'Google Ads', meta_ads: 'Meta Ads', spotify_ads: 'Spotify Ads', pinterest_ads: 'Pinterest Ads' };
type BudgetEditorMode = 'group' | 'campaign';

function periodOf(value: string) {
  return { year: Number(value.slice(0, 4)), month: Number(value.slice(5, 7)) };
}

function periodsInRange(start: string, end: string) {
  const first = periodOf(start); const last = periodOf(end);
  const values: { year: number; month: number }[] = [];
  for (let year = first.year, month = first.month; year < last.year || (year === last.year && month <= last.month);) {
    values.push({ year, month });
    month += 1;
    if (month > 12) { year += 1; month = 1; }
  }
  return values;
}

export function BudgetManager({ client, range, filters }: Props) {
  const eligible: CampaignReportingPlatform[] = useMemo(() => [
    ...(client.google_ads_sources.length ? ['google_ads' as const] : []),
    ...(client.meta_ads_sources.length ? ['meta_ads' as const] : []),
    ...((client.spotify_ads_sources?.length ?? 0) ? ['spotify_ads' as const] : []),
    ...((client.pinterest_ads_sources?.length ?? 0) ? ['pinterest_ads' as const] : []),
  ], [client]);
  const configurablePlatforms: ReportingPlatform[] = [
    'google_ads', 'meta_ads', 'spotify_ads', 'pinterest_ads',
  ];
  const availableGroupPlatforms = new Set<ReportingPlatform>([
    ...(client.google_ads_sources.length ? ['google_ads' as const] : []),
    ...(client.meta_ads_sources.length ? ['meta_ads' as const] : []),
    ...((client.spotify_ads_sources?.length ?? 0) ? ['spotify_ads' as const] : []),
    ...((client.pinterest_ads_sources?.length ?? 0) ? ['pinterest_ads' as const] : []),
  ]);
  const periods = useMemo(() => periodsInRange(range.start, range.end), [range.start, range.end]);
  const [selected, setSelected] = useState(() => periodOf(range.start));
  const { year, month } = selected;
  const [groups, setGroups] = useState<ReportingGroup[]>([]);
  const [savedGroups, setSavedGroups] = useState<ReportingGroup[]>([]);
  const [assignments, setAssignments] = useState<MonthlyCampaignAssignment[]>([]);
  const [savedAssignments, setSavedAssignments] = useState<MonthlyCampaignAssignment[]>([]);
  const [snapshotState, setSnapshotState] = useState<ReportingGroupSnapshotResult['state']>('not_configured');
  const [hasPrevious, setHasPrevious] = useState(false);
  const [canInitializeCurrent, setCanInitializeCurrent] = useState(false);
  const [budgets, setBudgets] = useState<MonthlyBudget[]>([]);
  const [campaigns, setCampaigns] = useState<CampaignChoice[]>([]);
  const [status, setStatus] = useState('Cargando configuración…');
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState('custom');
  const [groupsSaving, setGroupsSaving] = useState(false);
  const [configurationLoading, setConfigurationLoading] = useState(true);
  const [budgetSaving, setBudgetSaving] = useState(false);
  const [draftBudgets, setDraftBudgets] = useState<Record<string, string>>({});
  const [selectedCampaignKeys, setSelectedCampaignKeys] = useState<string[]>([]);
  const [bulkGroupId, setBulkGroupId] = useState('');
  const [editorMode, setEditorMode] = useState<BudgetEditorMode>('group');
  const budgetSavingLock = useRef(false);
  const configurationSavingLock = useRef(false);

  useEffect(() => { setSelected(periodOf(range.start)); }, [range.start, range.end]);

  const applySnapshot = (result: ReportingGroupSnapshotResult) => {
    const normalizedGroups = result.groups.map((group) => ({
      ...group,
      enabled_platforms: resolvedGroupPlatforms(group),
    }));
    setGroups(normalizedGroups);
    setSavedGroups(normalizedGroups);
    setAssignments(result.assignments);
    setSavedAssignments(result.assignments);
    setSnapshotState(result.state);
    setHasPrevious(result.has_previous_snapshot);
    setCanInitializeCurrent(result.can_initialize_from_current);
  };

  useEffect(() => {
    const ctrl = new AbortController();
    setConfigurationLoading(true);
    Promise.all([
      api.reportingGroupSnapshot(client.id, year, month, ctrl.signal),
      api.listBudgets(client.id, ctrl.signal),
    ]).then(([snapshot, budgetResult]) => {
      if (!ctrl.signal.aborted) { applySnapshot(snapshot); setBudgets(budgetResult); setStatus(''); setConfigurationLoading(false); }
    }).catch((error: unknown) => {
      if (!ctrl.signal.aborted) { setStatus(error instanceof Error ? error.message : String(error)); setConfigurationLoading(false); }
    });
    return () => ctrl.abort();
  }, [client.id, year, month]);

  useEffect(() => {
    let cancelled = false;
    const requests = eligible.flatMap((platform) => {
      const sources = platform === 'google_ads' ? client.google_ads_sources : platform === 'meta_ads' ? client.meta_ads_sources : platform === 'spotify_ads' ? client.spotify_ads_sources ?? [] : client.pinterest_ads_sources ?? [];
      return sources.map(async (source) => {
        const account = platform === 'google_ads'
          ? (source as ApiClient['google_ads_sources'][number]).customer_id
          : (source as ApiClient['meta_ads_sources'][number] | NonNullable<ApiClient['spotify_ads_sources']>[number] | NonNullable<ApiClient['pinterest_ads_sources']>[number]).ad_account_id;
        const result = await api.campaigns(platformApi[platform], account);
        const adsetCampaigns = platform === 'meta_ads'
          ? (source as ApiClient['meta_ads_sources'][number]).adset_selections?.map((item) => item.campaign_id) ?? []
          : [];
        const selectionIds = [...(('campaign_ids' in source ? source.campaign_ids : []) ?? []), ...adsetCampaigns];
        const selectedIds = selectionIds.length ? new Set(selectionIds) : null;
        const allowed = result.campaigns.filter((campaign) =>
          (!selectedIds || selectedIds.has(campaign.campaign_id)) &&
          !(campaign.assigned_to && campaign.assigned_to.id !== client.id && campaign.assigned_to.allocation === 'exclusive'),
        );
        return allowed.map((campaign) => ({
          key: `${platform}:${account}:${campaign.campaign_id}`, platform, account_id: account,
          campaign_id: campaign.campaign_id, name: campaign.campaign_name,
          objective: campaign.objective ?? campaign.advertising_channel_type,
          status: campaign.effective_status ?? campaign.status ?? 'UNKNOWN',
        } satisfies CampaignChoice));
      });
    });
    Promise.all(requests).then((lists) => { if (!cancelled) setCampaigns(lists.flat()); })
      .catch((error: unknown) => { if (!cancelled) setStatus(error instanceof Error ? error.message : String(error)); });
    return () => { cancelled = true; };
  }, [client.id, eligible.join(','), client.google_ads_sources, client.meta_ads_sources]);

  const assigned = useMemo(() => new Map<string, string>(
    assignments.filter((item) => item.reporting_group_id).map((item) => [campaignAssignmentKey(item), item.reporting_group_id!] as [string, string]),
  ), [assignments]);
  const budgetSummary = (platform: ReportingPlatform, groupId: string | null) => {
    const scoped = campaigns.filter((item) => item.platform === platform && (assigned.get(item.key) ?? null) === groupId);
    const allocations = scoped.map((campaign) => budgets.find((item) => item.platform === platform && item.year === year && item.month === month && item.account_id === campaign.account_id && item.campaign_id === campaign.campaign_id)).filter((item): item is MonthlyBudget => item != null);
    const cap = budgets.find((item) => item.platform === platform && item.year === year && item.month === month && !item.campaign_id && (item.reporting_group_id ?? null) === groupId);
    const allocated = allocations.reduce((sum, item) => sum + item.budget, 0);
    const formatter = new Intl.NumberFormat('es-MX', { style: 'currency', currency: client.currency });
    return { allocated, cap, effective: cap?.budget ?? (allocations.length ? allocated : null), remaining: cap ? cap.budget - allocated : null, formatter };
  };

  const persistConfiguration = async (nextGroups: ReportingGroup[], nextAssignments: MonthlyCampaignAssignment[]) => {
    if (configurationLoading || configurationSavingLock.current) return;
    configurationSavingLock.current = true;
    setGroupsSaving(true); setStatus('Guardando configuración del mes…');
    try {
      const result = await api.saveReportingGroupSnapshot(client.id, year, month, nextGroups, completeCampaignAssignments(nextAssignments, campaigns));
      applySnapshot(result); setStatus(`Configuración guardada para ${MONTH_LABELS[month - 1]} ${year}.`);
      window.dispatchEvent(new Event(BUDGETS_UPDATED_EVENT));
    } catch (error) { setStatus(error instanceof Error ? error.message : String(error)); }
    finally { configurationSavingLock.current = false; setGroupsSaving(false); }
  };

  const configurationDirty = JSON.stringify(groups) !== JSON.stringify(savedGroups)
    || JSON.stringify(assignments) !== JSON.stringify(savedAssignments);

  const createGroup = () => {
    const name = newName.trim();
    const id = name.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9_-]+/g, '-').replace(/^-|-$/g, '');
    if (!name || !id || groups.some((group) => group.id === id)) { setStatus('Escribe un nombre y usa un identificador único.'); return; }
    setGroups((current) => [...current, {
      id, name, type: newType, active: true, assignments: [],
      enabled_platforms: Object.fromEntries(REPORTING_PLATFORMS.map((platform) => [
        platform, platform !== 'spotify_ads' && configurablePlatforms.includes(platform),
      ])) as Record<ReportingPlatform, boolean>,
    }]);
    setNewName('');
  };

  const setPlatformEnabled = (groupId: string, platform: ReportingPlatform, enabled: boolean) => {
    setGroups((current) => setGroupPlatformEnabled(current, groupId, platform, enabled));
  };

  const moveCampaign = (campaign: CampaignChoice, groupId: string) => {
    const next = completeCampaignAssignments(assignments, campaigns).filter((item) => campaignAssignmentKey(item) !== campaign.key);
    next.push({ platform: campaign.platform, account_id: campaign.account_id, campaign_id: campaign.campaign_id, reporting_group_id: groupId || null });
    setAssignments(next);
  };

  const moveSelectedCampaigns = () => {
    const selected = visibleCampaigns.filter((campaign) => selectedCampaignKeys.includes(campaign.key));
    if (!selected.length) return;
    const next = new Map(completeCampaignAssignments(assignments, campaigns).map((item) => [campaignAssignmentKey(item), item] as const));
    for (const campaign of selected) next.set(campaign.key, {
      platform: campaign.platform, account_id: campaign.account_id, campaign_id: campaign.campaign_id,
      reporting_group_id: bulkGroupId || null,
    });
    setAssignments([...next.values()]);
    setSelectedCampaignKeys([]);
  };

  const saveBudget = async (platform: BudgetPlatform, groupId: string | null) => {
    if (budgetSavingLock.current) return;
    const key = `${year}-${month}:${groupId ?? ''}:${platform}`;
    const raw = draftBudgets[key] ?? String(budgets.find((item) => item.platform === platform && item.year === year && item.month === month && !item.campaign_id && (item.reporting_group_id ?? null) === groupId)?.budget ?? '');
    if (!raw.trim() || !Number.isFinite(Number(raw)) || Number(raw) < 0) { setStatus('Deja vacío un presupuesto no capturado o escribe un valor numérico igual o mayor que cero.'); return; }
    budgetSavingLock.current = true; setBudgetSaving(true); setStatus('Guardando presupuesto del grupo…');
    try {
      const saved = await api.saveBudget(client.id, platform, year, month, Number(raw), groupId);
      setBudgets((current) => [...current.filter((item) => !(item.platform === platform && item.year === year && item.month === month && !item.campaign_id && (item.reporting_group_id ?? null) === groupId)), saved]);
      window.dispatchEvent(new Event(BUDGETS_UPDATED_EVENT)); setStatus('Presupuesto guardado.');
    } catch (error) { setStatus(error instanceof Error ? error.message : String(error)); }
    finally { budgetSavingLock.current = false; setBudgetSaving(false); }
  };

  const clearBudget = async (platform: BudgetPlatform, groupId: string | null) => {
    if (budgetSavingLock.current) return;
    budgetSavingLock.current = true; setBudgetSaving(true); setStatus('Quitando presupuesto del grupo…');
    try {
      await api.deleteBudget(client.id, platform, year, month, groupId);
      setBudgets((current) => current.filter((item) => !(item.platform === platform && item.year === year && item.month === month && !item.campaign_id && (item.reporting_group_id ?? null) === groupId)));
      setDraftBudgets((current) => ({ ...current, [`${year}-${month}:${groupId ?? ''}:${platform}`]: '' }));
      window.dispatchEvent(new Event(BUDGETS_UPDATED_EVENT)); setStatus('Presupuesto eliminado; queda como no capturado.');
    } catch (error) { setStatus(error instanceof Error ? error.message : String(error)); }
    finally { budgetSavingLock.current = false; setBudgetSaving(false); }
  };

  const campaignBudgetKey = (campaign: CampaignChoice) => `${year}-${month}:${campaign.key}:campaign`;
  const saveCampaignBudget = async (campaign: CampaignChoice) => {
    if (budgetSavingLock.current) return;
    const key = campaignBudgetKey(campaign);
    const current = budgets.find((item) => item.platform === campaign.platform && item.year === year && item.month === month && item.account_id === campaign.account_id && item.campaign_id === campaign.campaign_id);
    const raw = draftBudgets[key] ?? String(current?.budget ?? '');
    if (!raw.trim() || !Number.isFinite(Number(raw)) || Number(raw) < 0) { setStatus('Captura un presupuesto mensual válido para la campaña.'); return; }
    budgetSavingLock.current = true; setBudgetSaving(true); setStatus('Guardando presupuesto de campaña…');
    try {
      const saved = await api.saveBudget(client.id, campaign.platform, year, month, Number(raw), null, { account_id: campaign.account_id, campaign_id: campaign.campaign_id });
      setBudgets((items) => [...items.filter((item) => !(item.platform === campaign.platform && item.year === year && item.month === month && item.account_id === campaign.account_id && item.campaign_id === campaign.campaign_id)), saved]);
      window.dispatchEvent(new Event(BUDGETS_UPDATED_EVENT)); setStatus('Presupuesto de campaña guardado.');
    } catch (error) { setStatus(error instanceof Error ? error.message : String(error)); }
    finally { budgetSavingLock.current = false; setBudgetSaving(false); }
  };

  const clearCampaignBudget = async (campaign: CampaignChoice) => {
    if (budgetSavingLock.current) return;
    budgetSavingLock.current = true; setBudgetSaving(true); setStatus('Quitando presupuesto de campaña…');
    try {
      await api.deleteBudget(client.id, campaign.platform, year, month, null, {
        account_id: campaign.account_id,
        campaign_id: campaign.campaign_id,
      });
      setBudgets((items) => items.filter((item) => !(
        item.platform === campaign.platform && item.year === year && item.month === month
        && item.account_id === campaign.account_id && item.campaign_id === campaign.campaign_id
      )));
      setDraftBudgets((items) => ({ ...items, [campaignBudgetKey(campaign)]: '' }));
      window.dispatchEvent(new Event(BUDGETS_UPDATED_EVENT)); setStatus('Presupuesto de campaña eliminado; queda como no capturado.');
    } catch (error) { setStatus(error instanceof Error ? error.message : String(error)); }
    finally { budgetSavingLock.current = false; setBudgetSaving(false); }
  };

  const campaignBudgetField = (campaign: CampaignChoice) => {
    const key = campaignBudgetKey(campaign);
    const found = budgets.find((item) => item.platform === campaign.platform && item.year === year && item.month === month && item.account_id === campaign.account_id && item.campaign_id === campaign.campaign_id);
    return <div className="campaign-budget-inline" key={`${campaign.key}:budget`}>
      <label>Presupuesto mensual ({client.currency})<input className="input" type="number" min="0" step="0.01" placeholder={found ? String(found.budget) : 'No capturado'} value={draftBudgets[key] ?? ''} onChange={(event) => setDraftBudgets((items) => ({ ...items, [key]: event.target.value }))} /></label>
      <button className="btn btn-sm" type="button" disabled={budgetSaving || configurationLoading} onClick={() => void saveCampaignBudget(campaign)}>Guardar</button>
      {found && <button className="btn btn-sm" type="button" disabled={budgetSaving || configurationLoading} onClick={() => void clearCampaignBudget(campaign)}>Quitar</button>}
    </div>;
  };

  const copyPrevious = async () => {
    try { applySnapshot(await api.copyPreviousReportingGroupSnapshot(client.id, year, month)); setStatus('Configuración copiada del mes anterior. Los presupuestos no se copiaron.'); }
    catch (error) { setStatus(error instanceof Error ? error.message : String(error)); }
  };

  const importCurrent = async () => {
    try { applySnapshot(await api.initializeReportingGroupSnapshotFromCurrent(client.id, year, month)); setStatus('La configuración actual se copió solo a este mes.'); }
    catch (error) { setStatus(error instanceof Error ? error.message : String(error)); }
  };

  const createEmptySnapshot = async () => {
    await persistConfiguration([], completeCampaignAssignments([], campaigns));
  };

  const budgetField = (platform: ReportingPlatform, groupId: string | null, groupName: string) => {
    const found = budgets.find((item) => item.platform === platform && item.year === year && item.month === month && !item.campaign_id && (item.reporting_group_id ?? null) === groupId);
    const key = `${year}-${month}:${groupId ?? ''}:${platform}`;
    return <div className="report-group-budget" key={key}>
      <strong>{BUDGET_PLATFORM_LABELS[platform]} · {groupName}</strong>
      <div><label>Presupuesto mensual ({client.currency})<input className="input" type="number" min="0" step="0.01" placeholder={found ? String(found.budget) : 'No capturado'} value={draftBudgets[key] ?? ''} onChange={(event) => setDraftBudgets((current) => ({ ...current, [key]: event.target.value }))} /></label>
      <button type="button" className="btn btn-primary" onClick={() => void saveBudget(platform, groupId)}>Guardar presupuesto</button>{found && <button type="button" className="btn" onClick={() => void clearBudget(platform, groupId)}>Quitar presupuesto</button>}</div>
      {found && <span className="muted small">Capturado: {new Intl.NumberFormat('es-MX', { style: 'currency', currency: found.currency }).format(found.budget)}</span>}
    </div>;
  };

  const periodOptions = useMemo(() => {
    const base = periods.length ? periods : [periodOf(range.start)];
    const first = base[0]; const last = base[base.length - 1];
    const adjacent = (year: number, month: number, offset: number) => {
      const value = new Date(Date.UTC(year, month - 1 + offset, 1));
      return { year: value.getUTCFullYear(), month: value.getUTCMonth() + 1 };
    };
    const options = [adjacent(first.year, first.month, -1), ...base, adjacent(last.year, last.month, 1)];
    return [...new Map(options.map((item) => [`${item.year}-${item.month}`, item])).values()];
  }, [periods, range.start]);
  const monthBudgetsCaptured = budgets.some((item) => item.year === year && item.month === month);
  const visibleCampaigns = campaigns.filter((campaign) => (filters?.platform == null || filters.platform === 'all' || filters.platform === campaign.platform)
    && (!filters?.visibleCampaignKeys || filters.visibleCampaignKeys.includes(campaign.key)));

  const campaignStatus = (value: string) => {
    const normalized = value.toUpperCase();
    if (normalized === 'ENABLED' || normalized === 'ACTIVE') return 'Activo';
    if (['PAUSED', 'REMOVED', 'ARCHIVED', 'CAMPAIGN_PAUSED', 'ADSET_PAUSED', 'WITH_ISSUES'].includes(normalized)) return 'Inactivo';
    return `Desconocido · ${value}`;
  };
  const campaignRows = (items: CampaignChoice[]) => items.map((campaign) => <div className="budget-campaign-row" key={campaign.key}>
    <div><strong>{campaign.name}</strong><span className="muted small">ID {campaign.campaign_id}</span></div>
    <span>{BUDGET_PLATFORM_LABELS[campaign.platform]}</span>
    <span className="muted small">Cuenta {campaign.account_id}</span>
    <select className="input" aria-label={`Grupo de ${campaign.name}`} value={assigned.get(campaign.key) ?? ''} disabled={groupsSaving || configurationLoading} onChange={(event) => moveCampaign(campaign, event.target.value)}><option value="">Sin grupo</option>{groups.map((group) => <option key={group.id} value={group.id} disabled={!group.active}>{group.name}{!group.active ? ' · inactivo' : ''}</option>)}</select>
    {campaignBudgetField(campaign)}
    <span className="small">{campaignStatus(campaign.status)}</span>
  </div>);

  return <section id="reporting-groups-budget" className="card reporting-groups-budget" aria-labelledby="budget-title" tabIndex={-1}>
    <h2 id="budget-title">Grupos y presupuestos · {MONTH_LABELS[month - 1]} {year}</h2>
    <p className="muted small">Cada configuración pertenece a un mes. Cambiar este mes no altera el periodo global ni los snapshots de otros meses.</p>
    <div className="budget-period-controls"><label>Presupuestos de<select className="input" aria-label="Mes de presupuestos" value={`${year}-${month}`} onChange={(event) => { const [nextYear, nextMonth] = event.target.value.split('-').map(Number); setSelected({ year: nextYear, month: nextMonth }); }}>{periodOptions.map((item) => <option key={`${item.year}-${item.month}`} value={`${item.year}-${item.month}`}>{MONTH_LABELS[item.month - 1]} {item.year}</option>)}</select></label></div>
    {!monthBudgetsCaptured && <p className="budget-empty-note">No hay presupuestos capturados para {MONTH_LABELS[month - 1]} {year}.</p>}
    <div className="budget-editor-tabs" role="tablist" aria-label="Modo de presupuestos">
      <button type="button" role="tab" aria-selected={editorMode === 'group'} onClick={() => setEditorMode('group')}>Por grupo</button>
      <button type="button" role="tab" aria-selected={editorMode === 'campaign'} onClick={() => setEditorMode('campaign')}>Por campaña</button>
    </div>
    {editorMode === 'group' && <>
    {snapshotState === 'missing_group_configuration' && <div className="card pacing-error" role="alert"><strong>Configuración de grupos no disponible para este periodo.</strong><p>El sistema no aplicará la configuración actual a un mes sin snapshot.</p><div className="report-group-actions">{hasPrevious && <button className="btn" type="button" onClick={() => void copyPrevious()} disabled={groupsSaving || configurationLoading}>Copiar configuración del mes anterior</button>}{canInitializeCurrent && <button className="btn" type="button" onClick={() => void importCurrent()} disabled={groupsSaving || configurationLoading}>Copiar configuración actual a este mes</button>}<button className="btn" type="button" onClick={() => void createEmptySnapshot()} disabled={groupsSaving || configurationLoading}>Crear snapshot vacío para este mes</button></div></div>}
    {snapshotState === 'not_configured' && hasPrevious && <button className="btn" type="button" onClick={() => void copyPrevious()} disabled={groupsSaving || configurationLoading}>Copiar configuración del mes anterior</button>}
    <div className="report-group-create"><label>Nuevo grupo<input className="input" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="p. ej. Conversión" /></label><label>Tipo sugerido<select className="input" value={newType} onChange={(event) => setNewType(event.target.value)}><option value="conversion">Conversión</option><option value="traffic">Tráfico</option><option value="leads">Leads</option><option value="awareness">Awareness</option><option value="custom">Personalizado</option></select></label><button className="btn" type="button" onClick={createGroup} disabled={groupsSaving || configurationLoading}>Crear grupo en {MONTH_LABELS[month - 1]}</button></div>
    {!!groups.length && <section className="group-platform-switches" aria-label="Plataformas activas por grupo"><h3>Plataformas activas por grupo</h3><p className="muted small">Desactivar una plataforma solo la excluye del reporting de este mes; conserva sus campañas y asignaciones.</p>{groups.map((group) => <div className="group-platform-switch-row" key={group.id}><strong>{group.name}</strong>{configurablePlatforms.map((platform) => <label key={platform}><input type="checkbox" aria-label={`${PLATFORM_NAMES[platform]} activo en ${group.name}`} checked={Boolean(group.enabled_platforms?.[platform])} disabled={groupsSaving || configurationLoading || !availableGroupPlatforms.has(platform)} onChange={(event) => setPlatformEnabled(group.id, platform, event.target.checked)} />{PLATFORM_NAMES[platform]} · {!availableGroupPlatforms.has(platform) ? 'No configurado' : group.enabled_platforms?.[platform] ? 'Activado' : 'Desactivado'}</label>)}</div>)}</section>}
    {eligible.map((platform) => {
      const platformCampaigns = visibleCampaigns.filter((campaign) => campaign.platform === platform);
      return <section className="reporting-platform-groups" key={platform}><h3>{BUDGET_PLATFORM_LABELS[platform]}</h3>
        <h4>Campañas</h4>
        {platformCampaigns.length > 0 && <div className="report-group-actions"><label><input type="checkbox" checked={platformCampaigns.every((campaign) => selectedCampaignKeys.includes(campaign.key))} onChange={(event) => setSelectedCampaignKeys((current) => event.target.checked ? [...new Set([...current, ...platformCampaigns.map((campaign) => campaign.key)])] : current.filter((key) => !platformCampaigns.some((campaign) => campaign.key === key)))} /> Seleccionar campañas visibles</label><select className="input" aria-label={`Grupo para selección ${platform}`} value={bulkGroupId} onChange={(event) => setBulkGroupId(event.target.value)}><option value="">Sin grupo</option>{groups.filter((group) => group.active).map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select><button type="button" className="btn" onClick={moveSelectedCampaigns} disabled={!platformCampaigns.some((campaign) => selectedCampaignKeys.includes(campaign.key))}>Asignar selección</button></div>}
        {platformCampaigns.length ? platformCampaigns.map((campaign) => <div className="report-group-campaign" key={campaign.key}><label className="campaign-select"><input aria-label={`Seleccionar ${campaign.name}`} type="checkbox" checked={selectedCampaignKeys.includes(campaign.key)} onChange={(event) => setSelectedCampaignKeys((current) => event.target.checked ? [...current, campaign.key] : current.filter((key) => key !== campaign.key))} /></label><div><strong>{campaign.name}</strong><span className="muted small">{campaign.objective || 'Objetivo no disponible'} · ID {campaign.campaign_id}</span></div><select className="input" aria-label={`Grupo de ${campaign.name}`} value={assigned.get(campaign.key) ?? ''} disabled={groupsSaving || configurationLoading} onChange={(event) => moveCampaign(campaign, event.target.value)}><option value="">Sin grupo</option>{groups.map((group) => <option key={group.id} value={group.id} disabled={!group.active}>{group.name}{!group.active ? ' · inactivo' : ''}</option>)}</select></div>) : <p className="muted small">No hay campañas que coincidan con los filtros actuales.</p>}
        {(() => { const info = budgetSummary(platform, null); return <div className="report-group-budget-block"><h4>General / sin grupo</h4><p className="muted small">Presupuesto efectivo: {info.effective == null ? 'No capturado' : info.formatter.format(info.effective)}{info.cap ? ` · asignado a campañas: ${info.formatter.format(info.allocated)} · saldo: ${info.formatter.format(info.remaining ?? 0)}` : ' · calculado desde presupuestos por campaña'}</p>{budgetField(platform, null, 'General / sin grupo')}</div>; })()}
        {groups.map((group) => <div className="report-group-budget-block" key={`${group.id}-${platform}`}><div className="report-group-heading"><h4>{group.name}</h4><div><span className="budget-group-state">{group.active ? 'Activo' : 'Inactivo'}</span>{eligible[0] === platform && <div className="report-group-actions"><button className="btn" type="button" onClick={() => { const name = window.prompt('Nombre del grupo', group.name)?.trim(); if (name) setGroups((current) => current.map((item) => item.id === group.id ? { ...item, name } : item)); }}>Renombrar</button><button className="btn" type="button" onClick={() => setGroups((current) => current.map((item) => item.id === group.id ? { ...item, active: !item.active } : item))}>{group.active ? `Desactivar en ${MONTH_LABELS[month - 1]}` : `Activar en ${MONTH_LABELS[month - 1]}`}</button></div>}</div></div>{(() => { const info = budgetSummary(platform, group.id); return <p className="muted small">{assignments.filter((item) => item.reporting_group_id === group.id && item.platform === platform).length} campañas asignadas. Presupuesto efectivo: {info.effective == null ? 'No capturado' : info.formatter.format(info.effective)}{info.cap ? ` · asignado a campañas: ${info.formatter.format(info.allocated)} · saldo: ${info.formatter.format(info.remaining ?? 0)}` : ' · calculado desde presupuestos por campaña'}{info.remaining != null && info.remaining < 0 && <strong className="budget-over-limit"> · Las asignaciones superan el límite.</strong>}</p>; })()}{group.active && budgetField(platform, group.id, group.name)}</div>)}
      </section>;
    })}
    {!groups.length && <p className="muted small">No hay grupos configurados para este mes. Puedes crear uno arriba; presupuesto sin grupo sigue disponible.</p>}
    </>}
    {configurationDirty && <div className="report-group-actions"><span className="muted small">Hay cambios sin guardar. Los filtros solo cambian la lista visible.</span><button type="button" className="btn btn-primary" onClick={() => void persistConfiguration(groups, assignments)} disabled={groupsSaving || configurationLoading}>Guardar grupos y asignaciones</button></div>}
    {editorMode === 'campaign' && <div className="budget-campaign-view">
      <p className="muted small">Cada presupuesto queda identificado por cliente, plataforma, cuenta, campaña y mes. La asignación de grupo es independiente del presupuesto capturado.</p>
      {!visibleCampaigns.length && <p className="card state muted">No hay campañas que coincidan con los filtros actuales.</p>}
      {eligible.map((platform) => {
        const items = visibleCampaigns.filter((campaign) => campaign.platform === platform);
        return <section className="reporting-platform-groups" key={platform}><h3>{BUDGET_PLATFORM_LABELS[platform]}</h3>
          <div className="budget-campaign-head"><strong>Campaña</strong><strong>Plataforma</strong><strong>Cuenta</strong><strong>Grupo</strong><strong>Presupuesto mensual</strong><strong>Estado</strong></div>
          {items.length ? campaignRows(items) : <p className="muted small">No hay campañas que coincidan con los filtros actuales.</p>}
        </section>;
      })}
    </div>}
    {status && <p className="muted small" role="status">{status}</p>}
  </section>;
}
