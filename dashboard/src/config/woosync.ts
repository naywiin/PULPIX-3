export interface WooSyncBranch {
  id: string;
  label: string;
}

/** Configuración única de sucursales reconocidas por WooSync. */
export const WOOSYNC_BRANCHES: readonly WooSyncBranch[] = [
  { id: 'nacional', label: 'Nacional' },
  { id: 'monterrey', label: 'Monterrey' },
  { id: 'saltillo', label: 'Saltillo' },
  { id: 'leon', label: 'León' },
  { id: 'chihuahua', label: 'Chihuahua' },
  { id: 'cdmx', label: 'CDMX' },
] as const;

export const WOOSYNC_CLIENT_IDS = new Set(['ssiento']);
export const WOOSYNC_DEFAULT_BRANCH = 'nacional';
/** Todas las sedes que WooSync devuelve como dimensiones independientes. */
export const WOOSYNC_ALL_BRANCH_IDS = WOOSYNC_BRANCHES
  .map((branch) => branch.id);

export function hasWooSyncSales(clientId: string | null | undefined): boolean {
  return Boolean(clientId && WOOSYNC_CLIENT_IDS.has(clientId));
}
