import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Sidebar } from '../components/Sidebar';
import { hasWooSyncSales, WOOSYNC_ALL_BRANCH_IDS, WOOSYNC_BRANCHES, WOOSYNC_DEFAULT_BRANCH } from '../config/woosync';
import { addYears } from './dates';
import { formatValue } from './format';
import { appliedFilters, buildWooSyncQuery, comparisonOverview, percentChange, yearDelta } from './woosync';
import { createWooSyncLoader, type WooSyncState } from './woosyncLoader';
import {
  filtersForBranchChange, filtersForCompareChange, SALES_KPIS, SalesContent, SalesResult, SalesSection,
  sameFilters, validateWooSyncDraftFilters,
} from '../sections/SalesSection';
import type { WooSyncFilters, WooSyncOverview } from '../types/woosync';

const fmt = { currency: 'MXN', locale: 'es-MX' };
const client = { id: 'ssiento', name: 'Ssiento', reportName: '', brandColor: '#000', currency: 'MXN', locale: 'es-MX', timezone: 'America/Mexico_City', sources: {} };
const selection = { preset: 'custom' as const, range: { start: '2026-09-01', end: '2026-09-29' }, compare: 'none' as const, compareRange: null };

const currentOverview = {
  total_sales: 96077.2, net_sales: 83097.2, gross_sales: 90135, orders_count: 46, unique_customers_count: 34,
  average_order_value: 2088.63, items_sold: 128, shipping_total: 12980, coupon_amount: 7432.6,
  refunds_total: 5, customers_count: 997, last_order_at: '2026-09-29 11:10:23',
};
const previousOverview = {
  total_sales: 152000, net_sales: 128800, gross_sales: 140000, orders_count: 70, unique_customers_count: 50,
  average_order_value: 2000, items_sold: 200, shipping_total: 12980, coupon_amount: 0,
};
const yearData: WooSyncOverview = {
  sales_overview: currentOverview,
  // el contrato conserva estas colecciones, pero la vista ya no las muestra
  daily_sales: [{ date: '2026-09-28', total_sales: 100 }], weekly_sales: [{ week: '2026-W39', total_sales: 300 }],
  monthly_sales: [{ month: '2026-09', total_sales: 300 }],
  top_products: [{ product_id: 7, name: 'Silla Norte', quantity: 2, total_sales: 200 }],
  top_categories: [{ name: 'Muebles', quantity: 2, total_sales: 200 }],
  top_customers: [{ customer_id: 8, name: 'Cliente Privado', orders_count: 2, total_spent: 200 }],
  order_statuses: [{ status: 'wc-completed', orders_count: 2 }],
  customers: { registered: 1, guests: 1, total: 2 },
  applied_filters: { branches: ['nacional'], from: '2026-09-01', to: '2026-09-29' },
  comparison: { enabled: true, type: 'previous_year', from: '2025-09-01', to: '2025-09-29', sales_overview: previousOverview },
};
const noComparison: WooSyncOverview = { ...yearData, comparison: null };

const base: WooSyncFilters = { from: '2026-09-01', to: '2026-09-29', branches: ['nacional'], compareTo: 'none' };

describe('navegación y consulta', () => {
  it('muestra Ventas solo para Ssiento', () => {
    expect(hasWooSyncSales('ssiento')).toBe(true);
    expect(hasWooSyncSales('blumia')).toBe(false);
    const visible = renderToStaticMarkup(<Sidebar page="overview" onNavigate={() => {}} open={false} onClose={() => {}} footer="" showSales />);
    const hidden = renderToStaticMarkup(<Sidebar page="overview" onNavigate={() => {}} open={false} onClose={() => {}} footer="" showSales={false} />);
    expect(visible).toContain('Ventas');
    expect(hidden).not.toContain('Ventas');
  });

  it('27. Todas las sedes incluye Nacional y la sede también se puede filtrar sola', () => {
    for (const branch of ['nacional', 'monterrey', 'saltillo', 'leon', 'chihuahua', 'cdmx']) {
      expect(buildWooSyncQuery({ ...base, branches: [branch] })).toContain(`branches=${branch}`);
    }
    const all = buildWooSyncQuery({ ...base, branches: [...WOOSYNC_ALL_BRANCH_IDS] });
    expect(all).toContain(`branches=${encodeURIComponent(WOOSYNC_ALL_BRANCH_IDS.join(','))}`);
    expect(all).toContain('nacional');
    expect(WOOSYNC_ALL_BRANCH_IDS).toContain('nacional');
    expect(WOOSYNC_DEFAULT_BRANCH).toBe('nacional');
    expect(WOOSYNC_BRANCHES.map((b) => b.id)).toEqual(['nacional', 'monterrey', 'saltillo', 'leon', 'chihuahua', 'cdmx']);
    const html = renderToStaticMarkup(<SalesSection client={client} selection={selection} onSelectionChange={() => {}} />);
    expect(html).toContain('<option value="nacional" selected="">Nacional</option>');
    expect(html).toContain('<option value="__all__">Todas las sedes</option>');
  });

  it('26. sucursal, fechas y comparación forman parte de la clave de consulta/cache', () => {
    const keys = [
      base,
      { ...base, branches: ['monterrey'] },
      { ...base, to: '2026-09-28' },
      { ...base, from: '2026-08-01', to: '2026-08-31' },
      { ...base, compareTo: 'previous_year' as const },
    ].map(buildWooSyncQuery);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys[4]).toContain('compare_to=previous_year');
    expect(keys[0]).not.toContain('compare_to');
  });

  it('valida el borrador de fechas', () => {
    const valid = { from: '2026-09-01', to: '2026-09-29', branches: ['nacional'], compareTo: 'none' as const };
    expect(validateWooSyncDraftFilters(valid).filters).toEqual(valid);
    expect(validateWooSyncDraftFilters({ ...valid, from: '2026-09-30' }).error).toContain('Desde');
    expect(validateWooSyncDraftFilters({ ...valid, to: '' }).error).toContain('obligatorios');
  });
});

describe('auto-actualización por sucursal y fechas pendientes', () => {
  it('1-2. cambiar de sucursal produce filtros nuevos (=> fetch) con la sede pedida', () => {
    const cdmx = filtersForBranchChange(base, ['cdmx']);
    expect(cdmx.branches).toEqual(['cdmx']);
    expect(sameFilters(base, cdmx)).toBe(false);
    expect(buildWooSyncQuery(cdmx)).toContain('branches=cdmx');
    const mty = filtersForBranchChange(cdmx, ['monterrey']);
    expect(buildWooSyncQuery(mty)).toContain('branches=monterrey');
    expect(mty.from).toBe('2026-09-01');
  });

  it('4. cambiar Desde/Hasta no toca los filtros aplicados hasta pulsar Actualizar', () => {
    // El borrador se separa de los aplicados: la sucursal usa las fechas APLICADAS, no las pendientes.
    const draft = { from: '2026-08-01', to: '2026-08-15', branches: ['nacional'], compareTo: 'none' as const };
    expect(filtersForCompareChange(base, draft, 'previous_year')).toBeNull();
    expect(filtersForBranchChange(base, ['cdmx']).from).toBe('2026-09-01');
    const html = renderToStaticMarkup(<SalesSection client={client} selection={selection} onSelectionChange={() => {}} />);
    expect(html).toContain('Actualizar');
  });

  it('cambiar comparación con periodo aplicado consulta automáticamente', () => {
    const draft = { from: base.from!, to: base.to!, branches: ['nacional'], compareTo: 'none' as const };
    expect(filtersForCompareChange(base, draft, 'previous_year')).toEqual({ ...base, compareTo: 'previous_year' });
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const overviewFor = (total: number): WooSyncOverview => ({ sales_overview: { total_sales: total } });

describe('3. respuestas viejas no sobrescriben las nuevas', () => {
  it('Nacional lenta → Monterrey → CDMX: solo CDMX llega a la UI', async () => {
    const pending = new Map<string, ReturnType<typeof deferred<WooSyncOverview>>>();
    const states: WooSyncState[] = [];
    const loader = createWooSyncLoader((filters) => {
      const d = deferred<WooSyncOverview>();
      pending.set(filters.branches[0], d); // el fetcher ignora la señal de aborto a propósito
      return d.promise;
    }, (state) => states.push(state));
    void loader.load({ ...base, branches: ['nacional'] });
    void loader.load({ ...base, branches: ['monterrey'] });
    void loader.load({ ...base, branches: ['cdmx'] });
    expect([...pending.keys()]).toEqual(['nacional', 'monterrey', 'cdmx']);
    pending.get('cdmx')!.resolve(overviewFor(3));
    await tick();
    pending.get('nacional')!.resolve(overviewFor(1)); // llega tarde
    pending.get('monterrey')!.reject(new Error('tarde'));
    await tick();
    const last = states[states.length - 1];
    expect(last.status).toBe('ready');
    expect(last.data?.sales_overview.total_sales).toBe(3);
    expect(states.filter((s) => s.status === 'error')).toHaveLength(0);
    expect(states.filter((s) => s.data?.sales_overview.total_sales === 1)).toHaveLength(0);
  });

  it('aborta la petición anterior y conserva la vista previa mientras carga', async () => {
    const signals: AbortSignal[] = [];
    const states: WooSyncState[] = [];
    const first = deferred<WooSyncOverview>();
    const loader = createWooSyncLoader((_f, signal) => { signals.push(signal); return signals.length === 1 ? Promise.resolve(overviewFor(1)) : first.promise; }, (s) => states.push(s));
    await loader.load(base);
    void loader.load({ ...base, branches: ['cdmx'] });
    expect(states[states.length - 1]).toMatchObject({ status: 'ready', refreshing: true });
    expect(states[states.length - 1].data?.sales_overview.total_sales).toBe(1);
    void loader.load({ ...base, branches: ['leon'] });
    expect(signals[1].aborted).toBe(true);
  });

  it('un error limpia los datos de la sede anterior y muestra el mensaje', async () => {
    const states: WooSyncState[] = [];
    let call = 0;
    const loader = createWooSyncLoader(async () => { call += 1; if (call === 2) throw new Error('WooSync respondió con HTTP 502.'); return overviewFor(1); }, (s) => states.push(s));
    await loader.load(base);
    await loader.load({ ...base, branches: ['cdmx'] });
    expect(states[states.length - 1]).toMatchObject({ status: 'error', data: null, error: 'WooSync respondió con HTTP 502.' });
  });

  it('muestra skeleton en la primera carga y atenúa (no borra) en las siguientes', () => {
    const props = { fmt, onRetry: () => {} };
    expect(renderToStaticMarkup(<SalesResult {...props} status="loading" data={null} refreshing={false} />)).toContain('Cargando métricas');
    const html = renderToStaticMarkup(<SalesResult {...props} status="ready" data={noComparison} refreshing />);
    expect(html).toContain('refreshing');
    expect(html).toContain('Ventas totales');
    expect(renderToStaticMarkup(<SalesResult {...props} status="error" data={null} refreshing={false} error="falló" />)).toContain('falló');
  });
});

describe('comparación contra año anterior', () => {
  it('5-7. el periodo comparado son exactamente las mismas fechas un año atrás', () => {
    expect(addYears('2026-09-01', -1)).toBe('2025-09-01');
    expect(addYears('2026-09-29', -1)).toBe('2025-09-29');
    expect(addYears('2026-03-15', -1)).toBe('2025-03-15');
    expect(addYears('2026-03-22', -1)).toBe('2025-03-22');
  });

  it('15. 29-feb bisiesto pasa a 28-feb sin fechas inválidas', () => {
    expect(addYears('2028-02-29', -1)).toBe('2027-02-28');
  });

  it('6. la misma sucursal viaja en la consulta con compare_to=previous_year', () => {
    const q = new URLSearchParams(buildWooSyncQuery({ ...base, branches: ['cdmx'], compareTo: 'previous_year' }));
    expect(q.get('branches')).toBe('cdmx');
    expect(q.get('from')).toBe('2026-09-01');
    expect(q.get('to')).toBe('2026-09-29');
    expect(q.get('compare_to')).toBe('previous_year');
    expect(q.has('top_limit')).toBe(false);
  });

  it('8. calcula ((actual - anterior) / anterior) * 100', () => {
    expect(percentChange(96077.2, 152000)).toBeCloseTo(-36.79, 1);
    expect(percentChange(46, 70)).toBeCloseTo(-34.29, 1);
    expect(percentChange(150, 100)).toBe(50);
    expect(yearDelta(96077.2, 152000).label).toBe('-36.8% vs año anterior');
    expect(yearDelta(150, 100).label).toBe('+50.0% vs año anterior');
    expect(yearDelta(83097.2, 128800).label).toBe('-35.5% vs año anterior');
  });

  it('9. anterior = 0, null o faltante nunca produce Infinity ni NaN', () => {
    for (const [current, previous] of [[10, 0], [0, 0], [null, 5], [5, null], [null, null], [Number.NaN, 3], [4, Number.POSITIVE_INFINITY]] as const) {
      const { label } = yearDelta(current, previous);
      expect(label).not.toMatch(/Infinity|NaN/);
    }
    expect(yearDelta(10, 0).label).toBe('N/A vs año anterior');
    expect(yearDelta(5, null).label).toBe('N/A vs año anterior');
    expect(yearDelta(0, 0).label).toBe('0.0% vs año anterior');
    const html = renderToStaticMarkup(<SalesContent data={yearData} fmt={fmt} />);
    expect(html).toContain('N/A vs año anterior'); // cupones: anterior = 0 con actual > 0
    expect(html).not.toMatch(/Infinity|NaN/);
  });

  it('10. sin comparación no muestra deltas', () => {
    const html = renderToStaticMarkup(<SalesContent data={noComparison} fmt={fmt} />);
    expect(html).not.toContain('vs año anterior');
    expect(html).not.toContain('delta');
  });

  it('11. con año anterior cada KPI muestra su delta', () => {
    const html = renderToStaticMarkup(<SalesContent data={yearData} fmt={fmt} />);
    expect(html).toContain('-36.8% vs año anterior');
    expect(html).toContain('-35.5% vs año anterior');
    expect(html).toContain('-34.3% vs año anterior');
    expect((html.match(/vs año anterior/g) ?? []).length).toBe(SALES_KPIS.length);
    expect(comparisonOverview(yearData)?.total_sales).toBe(152000);
    expect(comparisonOverview({ ...yearData, comparison: { enabled: false, type: 'previous_year', sales_overview: previousOverview } })).toBeNull();
  });

  it('14. el selector solo ofrece Sin comparación y Año anterior', () => {
    const html = renderToStaticMarkup(<SalesSection client={client} selection={selection} onSelectionChange={() => {}} />);
    expect(html).toContain('Sin comparación');
    expect(html).toContain('Año anterior');
    expect(html).not.toContain('Periodo anterior');
  });

  it('normaliza applied_filters confirmados', () => {
    expect(appliedFilters(yearData)).toEqual({ branches: ['nacional'], from: '2026-09-01', to: '2026-09-29' });
  });
});

describe('contenido de la vista simplificada', () => {
  const html = renderToStaticMarkup(<SalesContent data={yearData} fmt={fmt} />);
  const section = renderToStaticMarkup(<SalesSection client={client} selection={selection} onSelectionChange={() => {}} />);
  const all = html + section;

  it('25. conserva los 9 KPIs con sus valores', () => {
    expect(SALES_KPIS.map((k) => k.label)).toEqual([
      'Ventas totales', 'Ventas netas', 'Ventas brutas', 'Órdenes', 'Clientes únicos', 'Ticket promedio',
      'Productos vendidos', 'Total envío', 'Cupones',
    ]);
    for (const label of SALES_KPIS.map((k) => k.label)) expect(html).toContain(label);
    expect(html).toContain('Resumen ejecutivo');
    expect(html).toContain(formatValue(96077.2, 'currency', fmt));
    expect(html).toContain('$7,432.60');
    expect(html).toContain('128');
    expect((html.match(/class="card kpi"/g) ?? []).length).toBe(9);
  });

  it('12-15. no aparecen Reembolsos, Clientes registrados, Última orden ni Clientes invitados', () => {
    for (const label of ['Reembolsos', 'Clientes registrados', 'Última orden', 'Clientes invitados']) expect(all).not.toContain(label);
    expect(all).not.toContain('997'); // valor de customers_count
  });

  it('16-19. no hay series ni tabs Diario/Semanal/Mensual', () => {
    for (const label of ['Ventas diario', 'Ventas semanal', 'Ventas mensual', 'Diario', 'Semanal', 'Mensual', 'Granularidad']) expect(all).not.toContain(label);
    expect(all).not.toContain('2026-W39');
  });

  it('20-23. no aparecen las tablas inferiores ni sus datos', () => {
    for (const label of ['Productos más vendidos', 'Categorías principales', 'Mejores clientes', 'Estados de órdenes', 'Silla Norte', 'Muebles', 'Cliente Privado', 'Completadas']) expect(all).not.toContain(label);
    expect(all).not.toContain('data-table');
  });

  it('24. el control Top desapareció y no se envía top_limit', () => {
    expect(section).not.toMatch(/aria-label="Top"|>Top</);
    expect(buildWooSyncQuery(base)).not.toContain('top_limit');
  });

  it('muestra el estado vacío para una respuesta válida sin actividad', () => {
    expect(renderToStaticMarkup(<SalesContent data={{ sales_overview: { total_sales: 0 } }} fmt={fmt} />)).toContain('Sin ventas en el periodo');
    expect(renderToStaticMarkup(<SalesResult fmt={fmt} onRetry={() => {}} status="ready" data={null} refreshing={false} />)).toContain('Sin datos de WooSync');
  });
});
