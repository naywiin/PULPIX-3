import { describe, expect, it } from 'vitest';
import { CLIENTS } from './clients';
import { EXCLUDED_CLIENT_PATTERNS, isExcluded, withoutExcluded } from './exclusions';

describe('exclusión central', () => {
  it('ignora mayúsculas y coincide dentro del nombre', () => {
    for (const name of [
      'Vidanta', 'VIDANTA', 'vidanta', 'VidantaWorld', 'Grupo Vidanta', 'MCC vidanta 2026',
      'Vida Vacations', 'VIDA VACATIONS', 'Vida Vacations MX',
      'The View', 'THE VIEW', 'The View Cancun',
    ])
      expect(isExcluded(name)).toBe(true);
  });

  it('no excluye a los demás clientes', () => {
    for (const name of ['Blumia', 'Ssiento', 'Jungala', 'Cliente X'])
      expect(isExcluded(name)).toBe(false);
    expect(isExcluded(null, undefined, '')).toBe(false);
  });

  it('filtra por cualquiera de los campos que se le pasen', () => {
    const rows = [
      { name: 'Blumia Website', account_name: 'Cuenta Blumia' },
      { name: 'Propiedad X', account_name: 'VIDANTA' },
    ];
    expect(withoutExcluded(rows, (r) => [r.name, r.account_name])).toEqual([rows[0]]);
  });

  it('el registro mock no trae nombres excluidos', () => {
    expect(EXCLUDED_CLIENT_PATTERNS).toEqual(['vidanta', 'vida vacations', 'the view']);
    expect(CLIENTS.filter((c) => isExcluded(c.name, c.id))).toEqual([]);
  });
});
