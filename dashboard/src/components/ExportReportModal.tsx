import { useState } from 'react';
import { api } from '../api/backend';
import { validateReportPeriod } from '../lib/reporting';
import type { DateRange } from '../types/report';

interface Props {
  initialRange: DateRange;
  onClose: () => void;
}

export function ExportReportModal({ initialRange, onClose }: Props) {
  const [from, setFrom] = useState(initialRange.start);
  const [to, setTo] = useState(initialRange.end);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string>();

  const generate = async () => {
    const validation = validateReportPeriod(from, to);
    if (!validation.valid) {
      setError(validation.error);
      return;
    }
    setGenerating(true);
    setError(undefined);
    try {
      const report = await api.exportPerformanceReport(from, to);
      const url = URL.createObjectURL(report.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = report.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setGenerating(false);
    }
  };

  return <div className="modal-backdrop" role="presentation">
    <section className="card report-modal export-modal" role="dialog" aria-modal="true" aria-labelledby="export-title">
      <header className="report-modal-head"><div><h2 id="export-title">Exportar Excel</h2><p className="muted small">El archivo incluirá los clientes activos y sus fuentes publicitarias asignadas.</p></div><button type="button" className="btn-icon" onClick={onClose} aria-label="Cerrar">×</button></header>
      <fieldset className="export-period"><legend>Periodo del reporte</legend><label>Desde<input className="input" type="date" aria-label="Desde del reporte" value={from} onChange={(event) => setFrom(event.target.value)} /></label><label>Hasta<input className="input" type="date" aria-label="Hasta del reporte" value={to} onChange={(event) => setTo(event.target.value)} /></label></fieldset>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="report-modal-actions"><button type="button" className="btn" onClick={onClose} disabled={generating}>Cancelar</button><button type="button" className="btn btn-primary" onClick={generate} disabled={generating}>{generating ? 'Generando…' : 'Generar Excel'}</button></div>
    </section>
  </div>;
}
