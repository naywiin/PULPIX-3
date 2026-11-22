import type { ClientConfig } from '../config/clients';
import { COMPARE_PRESET_LABELS, RANGE_PRESET_LABELS, formatRange } from '../lib/dates';
import type { DateSelection } from '../types/report';

export function ClientLogo({ client }: { client: ClientConfig }) {
  if (client.logoUrl) return <img className="client-logo" src={client.logoUrl} alt={`Logo de ${client.name}`} />;
  const initials = client.name.slice(0, 2).toUpperCase();
  return (
    <div className="client-logo" style={{ background: client.brandColor }} aria-label={`Logo de ${client.name}`}>
      {initials}
    </div>
  );
}

export function ReportHeader({ client, selection, pageTitle }: { client: ClientConfig; selection: DateSelection; pageTitle: string }) {
  return (
    <div className="report-header card">
      <ClientLogo client={client} />
      <div className="report-title">
        <div className="muted small">{client.reportName}</div>
        <h1>
          {client.name} <span className="muted">· {pageTitle}</span>
        </h1>
      </div>
      <dl className="report-meta">
        <div>
          <dt>Periodo · {RANGE_PRESET_LABELS[selection.preset]}</dt>
          <dd>{formatRange(selection.range, true)}</dd>
        </div>
        <div>
          <dt>Comparación · {COMPARE_PRESET_LABELS[selection.compare]}</dt>
          <dd>{selection.compareRange ? formatRange(selection.compareRange, true) : '—'}</dd>
        </div>
      </dl>
    </div>
  );
}
