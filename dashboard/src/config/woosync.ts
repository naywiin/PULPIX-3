export interface WooSyncBranch {
  id: string;
  label: string;
}

/** Configuración de sucursales reconocidas por WooSync (opcional). */
export const WOOSYNC_BRANCHES: readonly WooSyncBranch[] = [
  { id: 'nacional', label: 'Nacional' },
  { id: 'monterrey', label: 'Monterrey' },
  { id: 'saltillo', label: 'Saltillo' },
  { id: 'leon', label: 'León' },
  { id: 'chihuahua', label: 'Chihuahua' },
  { id: 'cdmx', label: 'CDMX' },
] as const;

/** Sin clientes hardcodeados de terceros en Pulpix. */
export const WOOSYNC_CLIENT_IDS = new Set<string>();
export const WOOSYNC_DEFAULT_BRANCH = 'nacional';
export const WOOSYNC_ALL_BRANCH_IDS = WOOSYNC_BRANCHES.map((branch) => branch.id);

export function hasWooSyncSales(clientId: string | null | undefined): boolean {
  return Boolean(clientId && WOOSYNC_CLIENT_IDS.has(clientId));
}
