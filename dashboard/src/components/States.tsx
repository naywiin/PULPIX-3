export function KpiSkeleton({ count, className = '' }: { count: number; className?: string }) {
  return (
    <div className={`kpi-grid ${className}`.trim()} aria-busy="true" aria-label="Cargando métricas">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="card kpi skeleton-card">
          <div className="skeleton" style={{ width: '45%', height: 12 }} />
          <div className="skeleton" style={{ width: '70%', height: 28, marginTop: 14 }} />
          <div className="skeleton" style={{ width: '55%', height: 12, marginTop: 14 }} />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="card table-card" aria-busy="true">
      <div className="skeleton" style={{ width: 200, height: 14, margin: '16px' }} />
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ height: 32, margin: '8px 16px' }} />
      ))}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="card state state-error" role="alert">
      <div className="state-icon" aria-hidden>!</div>
      <div>
        <h3>No se pudieron cargar los datos</h3>
        <p>{message}</p>
        {onRetry && (
          <button type="button" className="btn" onClick={onRetry}>
            Reintentar
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Una plataforma caída o sin credenciales se muestra aislada ("Google Ads
 * temporalmente no disponible") sin afectar al resto del dashboard.
 */
export function PlatformUnavailable({ title, detail, onRetry }: { title: string; detail?: string; onRetry?: () => void }) {
  return (
    <div className="card state state-warn" role="alert">
      <div className="state-icon" aria-hidden>!</div>
      <div>
        <h3>{title}</h3>
        <p>Las demás plataformas siguen funcionando con normalidad.</p>
        {detail && <p className="muted small state-detail">Detalle: {detail}</p>}
        {onRetry && (
          <button type="button" className="btn" onClick={onRetry}>
            Reintentar
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="card state state-empty">
      <div className="state-icon" aria-hidden>∅</div>
      <div>
        <h3>{title}</h3>
        <p>{message}</p>
      </div>
    </div>
  );
}
