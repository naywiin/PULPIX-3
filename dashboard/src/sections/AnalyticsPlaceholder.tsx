import type { ClientConfig } from '../config/clients';
import { formatRange } from '../lib/dates';
import type { DateSelection } from '../types/report';
import { SectionTitle } from './common';

export function AnalyticsPlaceholder({ client, selection }: { client: ClientConfig; selection: DateSelection }) {
  const configured = Boolean(client.sources.google_analytics);

  return (
    <section className="report-section">
      <SectionTitle title="Google Analytics" tag="tag-ga" />
      <div className="card analytics-placeholder">
        <div className="analytics-mark" aria-hidden>GA</div>
        <div className="analytics-copy">
          <span className="eyebrow">Módulo preparado</span>
          <h2>Analítica web en la siguiente etapa</h2>
          <p>
            Esta vista queda reservada para sesiones, usuarios, eventos clave e ingresos de GA4. Por ahora no se consulta ninguna API ni se usan credenciales reales.
          </p>
          <div className="analytics-meta">
            <span><strong>Cliente</strong>{client.name}</span>
            <span><strong>Periodo</strong>{formatRange(selection.range, true)}</span>
            <span><strong>Fuente mock</strong>{configured ? 'Disponible para integración' : 'Sin configurar'}</span>
          </div>
        </div>
      </div>
    </section>
  );
}
