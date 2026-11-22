/**
 * Exclusión central de clientes y cuentas que no deben aparecer en el dashboard.
 *
 * Espejo de backend/app/exclusions.py: el backend ya filtra, esto es la segunda
 * barrera para que nada excluido llegue a un selector ni a "Descubrir cuentas".
 * Solo afecta a esta aplicación: no toca cuentas ni permisos reales en Google
 * Ads, Meta Ads o Google Analytics.
 */
export const EXCLUDED_CLIENT_PATTERNS = ['vidanta', 'vida vacations', 'the view'];

/** minúsculas + sin acentos + espacios colapsados, para comparar por subcadena. */
function normalize(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, ' ');
}

/** Comparación por subcadena, sin distinguir mayúsculas, acentos ni espacios sobrantes. */
export function isExcluded(...values: (string | null | undefined)[]): boolean {
  return values.some((v) => {
    if (!v) return false;
    const low = normalize(v);
    return low !== '' && EXCLUDED_CLIENT_PATTERNS.some((p) => low.includes(p));
  });
}

/** Quita de una lista todo lo que coincida con la exclusión. */
export function withoutExcluded<T>(items: T[], names: (item: T) => (string | null | undefined)[]): T[] {
  return items.filter((item) => !isExcluded(...names(item)));
}
