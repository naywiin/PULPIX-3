import { useEffect, useRef, useState } from 'react';
import {
  COMPARE_PRESET_LABELS,
  MONTHS_LONG,
  RANGE_PRESET_LABELS,
  addMonths,
  daysInMonth,
  formatRange,
  parseISO,
  presetRange,
  resolveCompare,
  toISO,
} from '../lib/dates';
import type { ComparePreset, DateRange, DateSelection, RangePreset } from '../types/report';

interface Props {
  value: DateSelection;
  today: string;
  onChange: (v: DateSelection) => void;
}

const RANGE_PRESETS: RangePreset[] = ['today', 'yesterday', 'last_7', 'last_30', 'this_month', 'last_month', 'this_year', 'custom'];
const COMPARE_PRESETS: ComparePreset[] = ['none', 'previous_period', 'previous_month', 'previous_year', 'custom'];
const WEEKDAYS = ['L', 'M', 'M', 'J', 'V', 'S', 'D'];

function MonthGrid({
  month,
  range,
  compare,
  today,
  onPick,
}: {
  month: string; // YYYY-MM-01
  range: DateRange;
  compare: DateRange | null;
  today: string;
  onPick: (iso: string) => void;
}) {
  const { y, m } = parseISO(month);
  const first = new Date(Date.UTC(y, m - 1, 1)).getUTCDay(); // 0 = domingo
  const offset = (first + 6) % 7; // semana inicia lunes
  const total = daysInMonth(y, m);
  const cells: (string | null)[] = [...Array(offset).fill(null), ...Array.from({ length: total }, (_, i) => toISO(y, m, i + 1))];

  return (
    <div className="month">
      <div className="month-title">
        {MONTHS_LONG[m - 1]} {y}
      </div>
      <div className="month-grid">
        {WEEKDAYS.map((d, i) => (
          <span key={i} className="wd">
            {d}
          </span>
        ))}
        {cells.map((iso, i) => {
          if (!iso) return <span key={i} />;
          const inRange = iso >= range.start && iso <= range.end;
          const inCompare = compare && iso >= compare.start && iso <= compare.end;
          const edge = iso === range.start || iso === range.end;
          const cls = ['day', inRange && 'in-range', edge && 'edge', inCompare && 'in-compare', iso === today && 'today']
            .filter(Boolean)
            .join(' ');
          return (
            <button key={iso} type="button" className={cls} disabled={iso > today} onClick={() => onPick(iso)}>
              {parseISO(iso).d}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function DateRangePicker({ value, today, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [pickingEnd, setPickingEnd] = useState(false);
  const [viewMonth, setViewMonth] = useState(() => value.range.end.slice(0, 8) + '01');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setDraft(value);
    setViewMonth(value.range.end.slice(0, 8) + '01');
    setPickingEnd(false);
    const onDoc = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, value]);

  const update = (patch: Partial<DateSelection>) =>
    setDraft((d) => {
      const next = { ...d, ...patch };
      const custom = next.compare === 'custom' ? next.compareRange ?? next.range : null;
      next.compareRange = resolveCompare(next.compare, next.range, custom);
      return next;
    });

  const pickPreset = (p: RangePreset) => {
    if (p === 'custom') return update({ preset: 'custom' });
    const range = presetRange(p, today);
    setViewMonth(range.end.slice(0, 8) + '01');
    update({ preset: p, range });
  };

  const pickDay = (iso: string) => {
    if (!pickingEnd) {
      update({ preset: 'custom', range: { start: iso, end: iso } });
      setPickingEnd(true);
    } else {
      const s = draft.range.start;
      update({ preset: 'custom', range: iso < s ? { start: iso, end: s } : { start: s, end: iso } });
      setPickingEnd(false);
    }
  };

  const setCustomCompare = (field: 'start' | 'end', v: string) => {
    if (!v) return;
    const base = draft.compareRange ?? draft.range;
    const r = { ...base, [field]: v };
    if (r.start > r.end) [r.start, r.end] = [r.end, r.start];
    setDraft((d) => ({ ...d, compare: 'custom', compareRange: r }));
  };

  const setCustomRange = (field: 'start' | 'end', v: string) => {
    if (!v) return;
    const r = { ...draft.range, [field]: v > today ? today : v };
    if (r.start > r.end) [r.start, r.end] = [r.end, r.start];
    update({ preset: 'custom', range: r });
  };

  const prevMonth = addMonths(viewMonth, -1);

  return (
    <div className="drp" ref={ref}>
      <button type="button" className="drp-trigger" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="drp-icon" aria-hidden>
          ▦
        </span>
        <span className="drp-text">
          <strong>{formatRange(value.range)}</strong>
          <span className="muted small">
            {value.compareRange ? `vs ${formatRange(value.compareRange)}` : COMPARE_PRESET_LABELS.none}
          </span>
        </span>
        <span aria-hidden className="caret">
          ▾
        </span>
      </button>

      {open && (
        <div className="drp-pop" role="dialog" aria-label="Seleccionar rango de fechas">
          <div className="drp-presets">
            <div className="drp-section-title">Periodo</div>
            {RANGE_PRESETS.map((p) => (
              <button key={p} type="button" className={draft.preset === p ? 'preset active' : 'preset'} onClick={() => pickPreset(p)}>
                {RANGE_PRESET_LABELS[p]}
              </button>
            ))}
          </div>

          <div className="drp-cal">
            <div className="drp-cal-nav">
              <button type="button" className="btn-icon" onClick={() => setViewMonth(addMonths(viewMonth, -1))} aria-label="Mes anterior">
                ‹
              </button>
              <span className="muted small">{pickingEnd ? 'Selecciona la fecha final' : 'Selecciona la fecha inicial'}</span>
              <button
                type="button"
                className="btn-icon"
                onClick={() => setViewMonth(addMonths(viewMonth, 1))}
                disabled={viewMonth >= today.slice(0, 8) + '01'}
                aria-label="Mes siguiente"
              >
                ›
              </button>
            </div>
            <div className="drp-months">
              <MonthGrid month={prevMonth} range={draft.range} compare={draft.compareRange} today={today} onPick={pickDay} />
              <MonthGrid month={viewMonth} range={draft.range} compare={draft.compareRange} today={today} onPick={pickDay} />
            </div>
            <div className="drp-inputs">
              <label>
                Inicio
                <input type="date" className="input" value={draft.range.start} max={today} onChange={(e) => setCustomRange('start', e.target.value)} />
              </label>
              <label>
                Fin
                <input type="date" className="input" value={draft.range.end} max={today} onChange={(e) => setCustomRange('end', e.target.value)} />
              </label>
            </div>
          </div>

          <div className="drp-compare">
            <div className="drp-section-title">Comparar con</div>
            {COMPARE_PRESETS.map((c) => (
              <label key={c} className="radio">
                <input
                  type="radio"
                  name="compare"
                  checked={draft.compare === c}
                  onChange={() => update({ compare: c, compareRange: c === 'custom' ? draft.compareRange : null })}
                />
                {COMPARE_PRESET_LABELS[c]}
              </label>
            ))}
            {draft.compare === 'custom' && (
              <div className="drp-inputs stacked">
                <label>
                  Inicio
                  <input type="date" className="input" value={draft.compareRange?.start ?? ''} onChange={(e) => setCustomCompare('start', e.target.value)} />
                </label>
                <label>
                  Fin
                  <input type="date" className="input" value={draft.compareRange?.end ?? ''} onChange={(e) => setCustomCompare('end', e.target.value)} />
                </label>
              </div>
            )}
            <div className="drp-summary small">
              <div>
                <span className="swatch swatch-range" /> {formatRange(draft.range)}
              </div>
              {draft.compareRange && (
                <div>
                  <span className="swatch swatch-compare" /> {formatRange(draft.compareRange)}
                </div>
              )}
            </div>
            <div className="drp-actions">
              <button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>
                Cancelar
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => {
                  onChange(draft);
                  setOpen(false);
                }}
              >
                Aplicar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
