export type WooSyncRow = Record<string, unknown>;
/** 'previous_year' lo construye nuestro backend (mismas fechas, un año atrás, misma sucursal). */
export type WooSyncCompare = 'none' | 'previous_year';

export interface WooSyncFilters {
  from?: string;
  to?: string;
  branches: string[];
  compareTo: WooSyncCompare;
}

export interface WooSyncOverview {
  sales_overview: WooSyncRow;
  daily_sales?: WooSyncRow[] | null;
  weekly_sales?: WooSyncRow[] | null;
  monthly_sales?: WooSyncRow[] | null;
  top_products?: WooSyncRow[] | null;
  top_categories?: WooSyncRow[] | null;
  top_customers?: WooSyncRow[] | null;
  order_statuses?: WooSyncRow[] | null;
  customers?: WooSyncRow | WooSyncRow[] | null;
  comparison?: WooSyncRow | null;
  applied_filters?: WooSyncRow | null;
  [key: string]: unknown;
}
