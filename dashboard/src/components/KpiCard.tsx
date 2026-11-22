import type { MetricDef } from '../config/metrics';
import { formatDelta, formatPercentChange, formatValue, type FormatOptions } from '../lib/format';
import { computeChange } from '../lib/metrics';

interface Props {
  metric: MetricDef;
  current: number | null;
  previous: number | null;
  fmt: FormatOptions;
  compareLabel?: string;
}

const SENTIMENT_LABEL = { positive: 'Mejora', negative: 'Empeora', neutral: 'Cambio neutral' } as const;

export function KpiCard({ metric, current, previous, fmt, compareLabel }: Props) {
  const change = previous != null && current != null ? computeChange(current, previous, metric.direction) : null;
  const arrow = change ? (change.trend === 'up' ? '↑' : change.trend === 'down' ? '↓' : '→') : null;

  return (
    <div className="card kpi">
      <div className="kpi-label">{metric.label}</div>
      <div className="kpi-value">{formatValue(current, metric.format, fmt)}</div>
      {change ? (
        <div className="kpi-compare">
          <span
            className={`delta delta-${change.sentiment}`}
            title={`${SENTIMENT_LABEL[change.sentiment]} vs ${compareLabel ?? 'periodo comparado'}`}
            aria-label={`${SENTIMENT_LABEL[change.sentiment]}: ${formatPercentChange(change.percent, fmt.locale)}`}
          >
            <span aria-hidden>{arrow}</span> {formatPercentChange(change.percent, fmt.locale)}
          </span>
          <span className="kpi-prev">vs {formatValue(change.previous, metric.format, fmt)}</span>
          <span className="kpi-abs">{formatDelta(change.absolute, metric.format, fmt)}</span>
        </div>
      ) : (
        <div className="kpi-compare muted">Sin comparación</div>
      )}
    </div>
  );
}
