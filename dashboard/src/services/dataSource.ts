import { api } from '../api/backend';
import type { DataSource } from '../types/dashboard';

/**
 * Decide si el dashboard usa el backend (REAL DATA) o los mocks locales (MOCK DATA).
 *
 *  - VITE_DATA_SOURCE=mock  → siempre mock
 *  - VITE_DATA_SOURCE=api   → siempre backend (si falla se muestra el error)
 *  - auto (por defecto)     → backend si /api/health responde; si no:
 *        · en desarrollo cae a mock (claramente marcado)
 *        · en producción NO usa mocks: muestra el error del backend
 *  - ?source=mock en la URL fuerza mock (solo en desarrollo).
 */
export type SourceSetting = 'auto' | 'api' | 'mock';

/** Preview marker used only by local development builds backed by synthetic fixtures. */
export function isSyntheticPreview(): boolean {
  const previewBuild = (import.meta.env as ImportMetaEnv & { VITE_SYNTHETIC_PREVIEW?: string }).VITE_SYNTHETIC_PREVIEW === 'true';
  return (import.meta.env.DEV || previewBuild) && typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('preview') === 'synthetic';
}

export interface SourceDecision {
  source: DataSource;
  /** Por qué se eligió (se muestra en la UI cuando es mock). */
  reason: string;
  backendUp: boolean;
  metaConfigured?: boolean;
}

function setting(): SourceSetting {
  const env = (import.meta.env.VITE_DATA_SOURCE as string | undefined)?.toLowerCase();
  if (import.meta.env.DEV && typeof window !== 'undefined') {
    const q = new URLSearchParams(window.location.search).get('source');
    if (q === 'mock' || q === 'api') return q;
  }
  return env === 'mock' || env === 'api' ? env : 'auto';
}

export async function detectDataSource(): Promise<SourceDecision> {
  const mode = setting();
  if (mode === 'mock') return { source: 'mock', reason: 'Modo mock forzado (VITE_DATA_SOURCE=mock o ?source=mock).', backendUp: false };
  try {
    const h = await api.health();
    return { source: 'real', reason: 'Backend conectado.', backendUp: true, metaConfigured: h.credentials?.meta_ads === 'configured' };
  } catch {
    if (mode === 'api' || import.meta.env.PROD) {
      return { source: 'real', reason: 'Backend no disponible.', backendUp: false };
    }
    return { source: 'mock', reason: 'Backend no disponible: usando datos mock de desarrollo.', backendUp: false };
  }
}
