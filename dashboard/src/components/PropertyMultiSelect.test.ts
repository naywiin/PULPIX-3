import { describe, expect, it } from 'vitest';
import { ga4Ids } from '../pages/SettingsClients';
import { summaryLabel } from './PropertyMultiSelect';

const client = (over: Partial<{ ga4_property_ids: string[]; ga4_property_id: string | null }>) => ({
  ga4_property_ids: [],
  ga4_property_id: null,
  ...over,
});

describe('resumen del selector cerrado', () => {
  it('dice cuántas propiedades hay seleccionadas', () => {
    expect(summaryLabel(2)).toBe('2 propiedades seleccionadas');
    expect(summaryLabel(5)).toBe('5 propiedades seleccionadas');
  });

  it('usa el singular con una sola', () => {
    expect(summaryLabel(1)).toBe('1 propiedad seleccionada');
  });

  it('sin selección muestra el pendiente de asignación', () => {
    expect(summaryLabel(0)).toBe('— Pendiente de asignación —');
  });
});

describe('ga4Ids: compatibilidad con el campo antiguo', () => {
  it('prefiere el array cuando existe', () => {
    expect(ga4Ids(client({ ga4_property_ids: ['264078195', '304259869'], ga4_property_id: '264078195' }))).toEqual([
      '264078195',
      '304259869',
    ]);
  });

  it('migra un registro antiguo con una sola propiedad', () => {
    expect(ga4Ids(client({ ga4_property_id: '496398757' }))).toEqual(['496398757']);
  });

  it('sin propiedades devuelve una lista vacía', () => {
    expect(ga4Ids(client({}))).toEqual([]);
  });
});
