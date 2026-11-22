import type { DataSource } from '../types/dashboard';
import { isSyntheticPreview } from '../services/dataSource';

/** Indicador siempre visible de la fuente de datos. REAL y MOCK nunca se mezclan en una vista. */
export function DataSourceBadge({ source, title }: { source: DataSource | null; title?: string }) {
  if (!source) return null;
  const synthetic = isSyntheticPreview();
  return (
    <span className={`source-badge source-${source}${synthetic ? ' source-synthetic' : ''}`} title={synthetic ? 'Vista previa local con datos sintéticos; no es información de producción.' : title} role="status">
      <span className="source-dot" aria-hidden />
      {synthetic ? 'DATOS SINTÉTICOS' : source === 'real' ? 'REAL DATA' : 'MOCK DATA'}
    </span>
  );
}
