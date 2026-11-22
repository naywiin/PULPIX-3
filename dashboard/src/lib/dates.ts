import type { ComparePreset, DateRange, DateSelection, RangePreset } from '../types/report';

/**
 * Utilidades de fechas trabajando con strings YYYY-MM-DD (fechas de calendario,
 * sin hora) para evitar corrimientos por zona horaria.
 */

export function parseISO(iso: string): { y: number; m: number; d: number } {
  const [y, m, d] = iso.split('-').map(Number);
  return { y, m, d };
}

export function toISO(y: number, m: number, d: number): string {
  // Normaliza desbordes (ej. día 0 = último día del mes anterior)
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.toISOString().slice(0, 10);
}

export function todayISO(now: Date = new Date()): string {
  return toISO(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

export function addDays(iso: string, days: number): string {
  const { y, m, d } = parseISO(iso);
  return toISO(y, m, d + days);
}

export function daysInMonth(y: number, m: number): number {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/** Mueve N meses conservando el día; si no existe (31 → feb) se ajusta al último día. */
export function addMonths(iso: string, months: number): string {
  const { y, m, d } = parseISO(iso);
  const total = y * 12 + (m - 1) + months;
  const ny = Math.floor(total / 12);
  const nm = (total % 12) + 1;
  return toISO(ny, nm, Math.min(d, daysInMonth(ny, nm)));
}

export function addYears(iso: string, years: number): string {
  return addMonths(iso, years * 12);
}

export function diffDays(a: string, b: string): number {
  const pa = parseISO(a);
  const pb = parseISO(b);
  return Math.round((Date.UTC(pb.y, pb.m - 1, pb.d) - Date.UTC(pa.y, pa.m - 1, pa.d)) / 86_400_000);
}

export function rangeLength(r: DateRange): number {
  return diffDays(r.start, r.end) + 1;
}

export function eachDay(r: DateRange): string[] {
  const out: string[] = [];
  for (let cur = r.start; cur <= r.end; cur = addDays(cur, 1)) out.push(cur);
  return out;
}

export function presetRange(preset: Exclude<RangePreset, 'custom'>, today: string): DateRange {
  const { y, m } = parseISO(today);
  switch (preset) {
    case 'today':
      return { start: today, end: today };
    case 'yesterday': {
      const yd = addDays(today, -1);
      return { start: yd, end: yd };
    }
    case 'last_7':
      // Últimos 7 días completos, sin incluir hoy
      return { start: addDays(today, -7), end: addDays(today, -1) };
    case 'last_30':
      return { start: addDays(today, -30), end: addDays(today, -1) };
    case 'this_month':
      return { start: toISO(y, m, 1), end: today };
    case 'last_month':
      return { start: toISO(y, m - 1, 1), end: toISO(y, m, 0) };
    case 'this_year':
      return { start: toISO(y, 1, 1), end: today };
  }
}

export function compareRangeFor(
  compare: Exclude<ComparePreset, 'none' | 'custom'>,
  range: DateRange,
): DateRange {
  switch (compare) {
    case 'previous_period': {
      const len = rangeLength(range);
      const end = addDays(range.start, -1);
      return { start: addDays(end, -(len - 1)), end };
    }
    case 'previous_month': {
      // Si el periodo es un mes completo, compara contra el mes completo anterior.
      const s = parseISO(range.start);
      const e = parseISO(range.end);
      const isFullMonth = s.d === 1 && e.d === daysInMonth(e.y, e.m) && s.y === e.y && s.m === e.m;
      if (isFullMonth) return { start: toISO(s.y, s.m - 1, 1), end: toISO(s.y, s.m, 0) };
      return { start: addMonths(range.start, -1), end: addMonths(range.end, -1) };
    }
    case 'previous_year':
      // Mismas fechas exactas del año anterior (1–24 sep 2026 → 1–24 sep 2025).
      return { start: addYears(range.start, -1), end: addYears(range.end, -1) };
  }
}

export function resolveCompare(
  compare: ComparePreset,
  range: DateRange,
  custom: DateRange | null,
): DateRange | null {
  if (compare === 'none') return null;
  if (compare === 'custom') return custom;
  return compareRangeFor(compare, range);
}

export function defaultSelection(today: string): DateSelection {
  const range = presetRange('this_month', today);
  return {
    preset: 'this_month',
    range,
    compare: 'previous_year',
    compareRange: compareRangeFor('previous_year', range),
  };
}

export const RANGE_PRESET_LABELS: Record<RangePreset, string> = {
  today: 'Hoy',
  yesterday: 'Ayer',
  last_7: 'Últimos 7 días',
  last_30: 'Últimos 30 días',
  this_month: 'Este mes',
  last_month: 'Mes anterior',
  this_year: 'Este año',
  custom: 'Personalizado',
};

export const COMPARE_PRESET_LABELS: Record<ComparePreset, string> = {
  none: 'Sin comparación',
  previous_period: 'Periodo anterior',
  previous_month: 'Mes anterior',
  previous_year: 'Año anterior',
  custom: 'Personalizado',
};

const MONTHS_SHORT = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
export const MONTHS_LONG = [
  'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
  'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
];

export function formatDate(iso: string, long = false): string {
  const { y, m, d } = parseISO(iso);
  return `${d} ${(long ? MONTHS_LONG : MONTHS_SHORT)[m - 1]} ${y}`;
}

export function formatRange(r: DateRange, long = false): string {
  if (r.start === r.end) return formatDate(r.start, long);
  return `${formatDate(r.start, long)} – ${formatDate(r.end, long)}`;
}
