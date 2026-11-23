/**
 * Exclusión opcional de clientes y cuentas en el frontend.
 *
 * En Pulpix no hay marcas excluidas hardcodeadas: cada usuario gestiona
 * libremente las cuentas conectadas de sus propios clientes.
 */
export const EXCLUDED_CLIENT_PATTERNS: string[] = [];

/** minúsculas + sin acentos + espacios colapsados, para comparar por subcadena. */
function normalize(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, ' ');
}

/** Comparación por subcadena, sin distinguir mayúsculas, acentos ni espacios sobrantes. */
export function isExcluded(...values: (string | null | undefined)[]): boolean {
  if (EXCLUDED_CLIENT_PATTERNS.length === 0) return false;
  return values.some((v) => {
    if (!v) return false;
    const low = normalize(v);
    return low !== '' && EXCLUDED_CLIENT_PATTERNS.some((p) => low.includes(p));
  });
}

/** Quita de una lista todo lo que coincida con la exclusión. */
export function withoutExcluded<T>(items: T[], names: (item: T) => (string | null | undefined)[]): T[] {
  if (EXCLUDED_CLIENT_PATTERNS.length === 0) return items;
  return items.filter((item) => !isExcluded(...names(item)));
}
