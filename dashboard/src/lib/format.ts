import type { MetricFormat } from '../config/metrics';

export interface FormatOptions {
  currency: string;
  locale: string;
}

const cache = new Map<string, Intl.NumberFormat>();
function nf(locale: string, opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = locale + JSON.stringify(opts);
  let f = cache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(locale, opts);
    cache.set(key, f);
  }
  return f;
}

export function formatValue(value: number | null | undefined, format: MetricFormat, o: FormatOptions): string {
  if (value == null || !Number.isFinite(value)) return '—';
  switch (format) {
    case 'currency':
      return nf(o.locale, { style: 'currency', currency: o.currency, currencyDisplay: 'narrowSymbol', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
    case 'number':
      return nf(o.locale, { maximumFractionDigits: 0 }).format(value);
    case 'decimal':
      return nf(o.locale, { maximumFractionDigits: 2 }).format(value);
    case 'percent':
      // Los porcentajes se guardan como fracción (0.2039 = 20.39%)
      return nf(o.locale, { style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
    case 'ratio':
      return `${nf(o.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)}x`;
  }
}

/** Cambio absoluto con signo, en el mismo formato que la métrica. */
export function formatDelta(delta: number | null, format: MetricFormat, o: FormatOptions): string {
  if (delta == null || !Number.isFinite(delta)) return '—';
  const sign = delta > 0 ? '+' : delta < 0 ? '−' : '±';
  if (format === 'percent') {
    // Diferencia de tasas en puntos porcentuales
    return `${sign}${nf(o.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(delta) * 100)} pp`;
  }
  return `${sign}${formatValue(Math.abs(delta), format, o)}`;
}

export function formatPercentChange(pct: number | null, locale: string): string {
  if (pct == null || !Number.isFinite(pct)) return '—';
  const sign = pct > 0 ? '+' : pct < 0 ? '−' : '';
  return `${sign}${nf(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(pct) * 100)}%`;
}
