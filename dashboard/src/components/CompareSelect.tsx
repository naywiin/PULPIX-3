import { COMPARE_PRESET_LABELS, resolveCompare } from '../lib/dates';
import type { ComparePreset, DateSelection } from '../types/report';

const OPTIONS: ComparePreset[] = ['none', 'previous_period', 'previous_month', 'previous_year', 'custom'];

/**
 * Selector rápido de comparación. "Personalizado" conserva el rango comparado
 * actual (o el mismo periodo) y se ajusta en el calendario del selector de fechas.
 */
export function CompareSelect({ value, onChange }: { value: DateSelection; onChange: (v: DateSelection) => void }) {
  return (
    <label className="compare-select">
      <span className="muted small">Comparar</span>
      <select
        className="input"
        value={value.compare}
        onChange={(e) => {
          const compare = e.target.value as ComparePreset;
          const custom = compare === 'custom' ? value.compareRange ?? value.range : null;
          onChange({ ...value, compare, compareRange: resolveCompare(compare, value.range, custom) });
        }}
      >
        {OPTIONS.map((o) => (
          <option key={o} value={o}>
            {COMPARE_PRESET_LABELS[o]}
          </option>
        ))}
      </select>
    </label>
  );
}
