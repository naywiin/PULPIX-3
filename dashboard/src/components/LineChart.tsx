import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { formatDate } from '../lib/dates';

export interface ChartSeries {
  key: string;
  label: string;
  /** Token CSS (ej. "var(--series-gads)"). El color sigue a la entidad, nunca al orden. */
  color: string;
  dash?: boolean;
  values: (number | null)[];
}

interface Props {
  title: string;
  subtitle?: string;
  dates: string[];
  series: ChartSeries[];
  format: (v: number) => string;
  height?: number;
  /** Muestra la leyenda aun con una sola serie (cuando la identidad importa, ej. plataformas). */
  legend?: boolean;
}

const PAD = { top: 12, right: 200, bottom: 28, left: 92 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  const nice = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => f <= s) ?? 10;
  return nice * exp;
}

/** Gráfica de líneas en SVG con crosshair, tooltip único para todas las series y vista de tabla. */
export function LineChart({ title, subtitle, dates, series, format, height = 240, legend = false }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const titleId = useId();

  const empty = dates.length === 0 || series.every((s) => s.values.every((v) => !v));

  // Se re-engancha cuando el contenedor SVG se monta (tras cargar datos o volver de la tabla).
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.floor(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [showTable, empty]);

  const narrow = width < 520;
  const pad = { ...PAD, right: narrow ? 16 : PAD.right, left: narrow ? 84 : PAD.left };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const max = useMemo(() => niceMax(Math.max(0, ...series.flatMap((s) => s.values.map((v) => v ?? 0)))), [series]);
  const n = dates.length;
  const x = (i: number) => pad.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const y = (v: number) => pad.top + innerH - (v / max) * innerH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const xLabelEvery = Math.max(1, Math.ceil(n / (narrow ? 4 : 8)));

  const paths = series.map((s) => {
    let d = '';
    s.values.forEach((v, i) => {
      if (v == null) return;
      d += `${d ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    });
    return d;
  });

  // Etiquetas directas al final de cada línea, separadas si chocan.
  const endLabels = useMemo(() => {
    if (narrow || series.length < 2) return [];
    const items = series
      .map((s) => {
        let idx = s.values.length - 1;
        while (idx >= 0 && s.values[idx] == null) idx--;
        return idx < 0 ? null : { key: s.key, label: s.label, color: s.color, yy: y(s.values[idx] ?? 0) };
      })
      .filter((v): v is NonNullable<typeof v> => v != null)
      .sort((a, b) => a.yy - b.yy);
    for (let i = 1; i < items.length; i++) if (items[i].yy - items[i - 1].yy < 14) items[i].yy = items[i - 1].yy + 14;
    return items;
  }, [series, narrow, max, height]);

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const i = n <= 1 ? 0 : Math.round(((px - pad.left) / innerW) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowRight') setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
    else if (e.key === 'ArrowLeft') setHover((h) => Math.max(0, (h ?? n) - 1));
    else if (e.key === 'Escape') setHover(null);
  };

  return (
    <section className="card chart-card" aria-labelledby={titleId}>
      <header className="chart-head">
        <div>
          <h3 id={titleId}>{title}</h3>
          {subtitle && <p className="muted small">{subtitle}</p>}
        </div>
        <div className="chart-tools">
          {(series.length > 1 || legend) && (
            <ul className="legend" aria-label="Leyenda">
              {series.map((s) => (
                <li key={s.key}>
                  <span className="legend-line" style={s.dash ? { background: 'transparent', borderTop: `2px dashed ${s.color}` } : { background: s.color }} aria-hidden />
                  {s.label}
                </li>
              ))}
            </ul>
          )}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowTable((v) => !v)} aria-pressed={showTable}>
            {showTable ? 'Ver gráfica' : 'Ver tabla'}
          </button>
        </div>
      </header>

      {empty ? (
        <div className="chart-empty muted">Sin datos diarios para el periodo.</div>
      ) : showTable ? (
        <div className="table-scroll chart-table">
          <table className="data-table">
            <thead>
              <tr>
                <th className="col-name">Fecha</th>
                {series.map((s) => (
                  <th key={s.key} className="num">
                    {s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dates.map((d, i) => (
                <tr key={d}>
                  <td className="col-name">{formatDate(d)}</td>
                  {series.map((s) => (
                    <td key={s.key} className="num">
                      {s.values[i] == null ? '—' : format(s.values[i] as number)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart-wrap" ref={wrapRef}>
          <svg
            width="100%"
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label={`${title}. Usa las flechas para recorrer los días.`}
            tabIndex={0}
            onKeyDown={onKey}
            onBlur={() => setHover(null)}
          >
            {ticks.map((t) => (
              <g key={t}>
                <line className="chart-grid" x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} />
                <text className="chart-axis" x={pad.left - 8} y={y(t)} dy="0.32em" textAnchor="end">
                  {format(t)}
                </text>
              </g>
            ))}
            {dates.map((d, i) =>
              // Etiqueta cada N días + el último, omitiendo la que chocaría con el último.
              (i % xLabelEvery === 0 && n - 1 - i >= xLabelEvery / 2) || i === n - 1 ? (
                <text key={d} className="chart-axis" x={x(i)} y={height - 8} textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>
                  {formatDate(d).replace(/ \d{4}$/, '')}
                </text>
              ) : null,
            )}
            {hover != null && <line className="chart-crosshair" x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} />}
            {series.map((s, si) => (
              <path key={s.key} d={paths[si]} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dash ? '7 5' : undefined} />
            ))}
            {hover != null &&
              series.map((s) =>
                s.values[hover] == null ? null : (
                  <circle key={s.key} cx={x(hover)} cy={y(s.values[hover] as number)} r={4} fill={s.color} className="chart-dot" />
                ),
              )}
            {endLabels.map((l) => (
              <text key={l.key} className="chart-end-label" x={width - pad.right + 8} y={l.yy} dy="0.32em">
                {l.label}
              </text>
            ))}
            <rect
              x={pad.left}
              y={pad.top}
              width={innerW}
              height={innerH}
              fill="transparent"
              onPointerMove={onMove}
              onPointerLeave={() => setHover(null)}
            />
          </svg>
          {hover != null && (
            <div
              className="chart-tooltip"
              role="status"
              style={{
                left: `${(x(hover) / width) * 100}%`,
                transform: x(hover) > width * 0.6 ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)',
              }}
            >
              <div className="muted small">{formatDate(dates[hover])}</div>
              {series.map((s) => (
                <div key={s.key} className="tt-row">
                  <span className="legend-line" style={{ background: s.color }} aria-hidden />
                  <strong>{s.values[hover] == null ? '—' : format(s.values[hover] as number)}</strong>
                  <span className="muted small">{s.label}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
