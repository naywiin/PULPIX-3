import { describe, expect, it } from 'vitest';
import { pacingCells } from './PerformancePacingSection';

describe('resultado Meta en Performance/Pacing', () => {
  for (const id of ['cirque-du-soleil-joya', 'jungala', 'ssiento', 'blumia']) {
    it(`${id} usa Compras y Costo por resultado`, () => {
      const labels = pacingCells(id, [{ platform: 'meta_ads' }]).map((cell) => cell.label);
      expect(labels).toContain('Compras');
      expect(labels).toContain('Costo por resultado');
      expect(labels).not.toContain('Clientes potenciales');
    });
  }
  it('oculta Leads de la superficie de pacing de los perfiles de compra', () => {
    const labels = pacingCells('cirque-du-soleil-joya', [{ platform: 'meta_ads', leads: 2 }]).map((cell) => cell.label);
    expect(labels).toContain('Compras');
    expect(labels).not.toContain('Clientes potenciales');
  });
});
