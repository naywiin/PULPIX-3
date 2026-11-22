import { useMemo, useState, type ReactNode } from 'react';
import type { MetricFormat } from '../config/metrics';
import { formatValue, type FormatOptions } from '../lib/format';

export interface Column<T> {
  key: string;
  label: string;
  /** Valor para ordenar/formatear. */
  value: (row: T) => string | number | null;
  format?: MetricFormat;
  render?: (row: T) => ReactNode;
  /** Valor del total; null → "—". Omitido → sin celda de total. */
  total?: number | null;
}

interface Props<T> {
  title: string;
  subtitle?: string;
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  fmt: FormatOptions;
  searchPlaceholder?: string;
  initialSort?: { key: string; dir: 'asc' | 'desc' };
  pageSizes?: number[];
  emptyMessage?: string;
}

export function DataTable<T>({
  title,
  subtitle,
  rows,
  columns,
  rowKey,
  fmt,
  searchPlaceholder = 'Buscar campaña…',
  initialSort,
  pageSizes = [10, 25, 50],
  emptyMessage = 'No hay datos para el periodo seleccionado.',
}: Props<T>) {
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState(initialSort ?? { key: columns[1]?.key ?? columns[0].key, dir: 'desc' as const });
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(pageSizes[0]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const first = columns[0];
    const base = q ? rows.filter((r) => String(first.value(r) ?? '').toLowerCase().includes(q)) : rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return base;
    return [...base].sort((a, b) => {
      const va = col.value(a);
      const vb = col.value(b);
      const numeric = (typeof va === 'number' || va == null) && (typeof vb === 'number' || vb == null);
      const cmp = numeric
        ? (va ?? -Infinity) === (vb ?? -Infinity) ? 0 : (va ?? -Infinity) < (vb ?? -Infinity) ? -1 : 1
        : String(va ?? '').localeCompare(String(vb ?? ''), 'es');
      return sort.dir === 'asc' ? cmp : -cmp;
    });
  }, [rows, columns, query, sort]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const visible = filtered.slice(safePage * pageSize, (safePage + 1) * pageSize);
  const hasTotals = columns.some((c) => c.total !== undefined);

  const toggleSort = (key: string) => {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === columns[0].key ? 'asc' : 'desc' }));
    setPage(0);
  };

  const cell = (c: Column<T>, r: T) => (c.render ? c.render(r) : c.format ? formatValue(c.value(r) as number, c.format, fmt) : c.value(r));

  return (
    <section className="card table-card">
      <header className="table-head">
        <div>
          <h3>{title}</h3>
          {subtitle && <p className="muted small">{subtitle}</p>}
        </div>
        <input
          type="search"
          className="input search"
          placeholder={searchPlaceholder}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
          }}
          aria-label={searchPlaceholder}
        />
      </header>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((c, i) => {
                const active = sort.key === c.key;
                return (
                  <th
                    key={c.key}
                    className={i === 0 ? 'col-name' : 'num'}
                    aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button type="button" onClick={() => toggleSort(c.key)} className={active ? 'sort active' : 'sort'}>
                      {c.label}
                      <span className="sort-ind" aria-hidden>
                        {active ? (sort.dir === 'asc' ? '▲' : '▼') : '↕'}
                      </span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="empty-row">
                  {query ? `Ninguna campaña coincide con “${query}”.` : emptyMessage}
                </td>
              </tr>
            ) : (
              visible.map((r) => (
                <tr key={rowKey(r)}>
                  {columns.map((c, i) => (
                    <td key={c.key} className={i === 0 ? 'col-name' : 'num'}>
                      {cell(c, r)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
          {hasTotals && rows.length > 0 && (
            <tfoot>
              <tr>
                {columns.map((c, i) => (
                  <td key={c.key} className={i === 0 ? 'col-name' : 'num'}>
                    {i === 0 ? 'Total general' : c.total === undefined ? '' : formatValue(c.total, c.format ?? 'number', fmt)}
                  </td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      <footer className="table-foot">
        <label className="muted small">
          Filas por página{' '}
          <select
            className="input select-sm"
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value));
              setPage(0);
            }}
          >
            {pageSizes.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small">
          {filtered.length === 0 ? '0' : `${safePage * pageSize + 1}–${Math.min(filtered.length, (safePage + 1) * pageSize)}`} de {filtered.length}
        </span>
        <div className="pager">
          <button type="button" className="btn-icon" onClick={() => setPage(safePage - 1)} disabled={safePage === 0} aria-label="Página anterior">
            ‹
          </button>
          <button type="button" className="btn-icon" onClick={() => setPage(safePage + 1)} disabled={safePage >= pageCount - 1} aria-label="Página siguiente">
            ›
          </button>
        </div>
      </footer>
    </section>
  );
}
