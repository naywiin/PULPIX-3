import { useState } from 'react';
import type { DateRange } from '../types/report';
import { ExportReportModal } from './ExportReportModal';

export function ReportingActions({ range }: { range: DateRange }) {
  const [showExport, setShowExport] = useState(false);
  const openBudgets = () => {
    if (!window.location.hash.endsWith('/overview')) window.location.hash = '/overview';
    window.setTimeout(() => {
      const target = document.getElementById('reporting-groups-budget');
      if (!target) return;
      const stickyHeader = document.querySelector<HTMLElement>('.topbar')?.getBoundingClientRect().height ?? 0;
      const targetTop = target.getBoundingClientRect().top + window.scrollY - stickyHeader - 16;
      window.scrollTo({ top: Math.max(0, targetTop), behavior: 'smooth' });
      target.focus({ preventScroll: true });
    }, 120);
  };
  return <>
    <div className="reporting-actions">
      <button type="button" className="btn" onClick={openBudgets}>Administrar presupuestos</button>
      <button type="button" className="btn btn-primary" onClick={() => setShowExport(true)}>Exportar Excel</button>
    </div>
    {showExport && <ExportReportModal initialRange={range} onClose={() => setShowExport(false)} />}
  </>;
}
