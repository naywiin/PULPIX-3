import { KpiCard } from '../components/KpiCard';
import { EmptyState, PlatformUnavailable } from '../components/States';
import type { BlockWarning, PlatformBlock } from '../types/dashboard';
import type { MetricDef } from '../config/metrics';
import type { FormatOptions } from '../lib/format';

export function KpiRow({
  metrics,
  current,
  previous,
  fmt,
  compareLabel,
}: {
  metrics: MetricDef[];
  /** Cualquier objeto con las claves de las métricas (Totals, métricas GA4, combinados…). */
  current: object;
  previous: object | null;
  fmt: FormatOptions;
  compareLabel?: string;
}) {
  const get = (t: object, key: string): number | null => {
    const values = t as Record<string, unknown>;
    const denominator = ({ cpc: 'clicks', ctr: 'impressions', cpa: 'conversions', roas: 'spend', cost_per_result: 'leads', cost_per_purchase: 'purchases', conversion_rate: 'clicks' } as Record<string, string>)[key];
    if (denominator && (values[denominator] == null || values[denominator] === 0)) return null;
    const v = values[key];
    return typeof v === 'number' && Number.isFinite(v) ? v : null;
  };
  return (
    <div className={`kpi-grid kpi-grid-${metrics.length}`}>
      {metrics.map((m) => (
        <KpiCard
          key={m.label}
          metric={m}
          current={get(current, m.key)}
          previous={previous ? get(previous, m.key) : null}
          fmt={fmt}
          compareLabel={compareLabel}
        />
      ))}
    </div>
  );
}

/**
 * Avisos de un bloque: fuentes que fallaron (con su ID) y avisos globales.
 * Nunca sustituyen a los datos: se muestran ENCIMA de lo que sí se pudo cargar.
 */
export function BlockWarnings({ warnings, noun }: { warnings: BlockWarning[]; noun: string }) {
  if (warnings.length === 0) return null;
  return (
    <div className="card state state-warn ga-warnings" role="status">
      <div className="state-icon" aria-hidden>
        !
      </div>
      <div>
        <h3>{warnings.length === 1 ? 'Un aviso' : `${warnings.length} avisos`}</h3>
        <p>Los datos mostrados corresponden a las fuentes que sí respondieron.</p>
        <ul className="ga-warning-list small">
          {warnings.map((w, i) => {
            const id = w.accountId ?? w.propertyId;
            return (
              <li key={`${id ?? 'global'}-${i}`}>
                {id && <strong>{noun} {id}: </strong>}
                {w.message}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

export function SectionTitle({ title, tag, children }: { title: string; tag: string; children?: React.ReactNode }) {
  return (
    <div className="section-title">
      <span className={`platform-tag ${tag}`} aria-hidden />
      <h2>{title}</h2>
      {children}
    </div>
  );
}

export interface BlockViewProps<P> {
  block: PlatformBlock<P> | null;
  /** Primera carga sin datos todavía. */
  loading: boolean;
  onRetry: () => void;
}

/**
 * Resuelve los estados comunes de una plataforma: cargando, no asignada,
 * no disponible (error aislado) o sin actividad. Devuelve null si hay datos.
 */
export function blockStatusView<P>(
  { block, loading, onRetry }: BlockViewProps<P>,
  opts: { clientName: string; skeleton: React.ReactNode; isEmpty: (p: P) => boolean },
): React.ReactNode | null {
  if (loading || !block) return opts.skeleton;
  if (block.status === 'not_linked')
    return <EmptyState title={`${block.label} sin cuenta asignada`} message={block.message ?? `${opts.clientName} no tiene una cuenta de ${block.label} asignada. Asígnala en Configuración → Clientes.`} />;
  if (block.status !== 'ok' || !block.current)
    return <PlatformUnavailable title={block.message ?? `${block.label} temporalmente no disponible`} detail={block.detail} onRetry={onRetry} />;
  if (opts.isEmpty(block.current) && (!block.previous || opts.isEmpty(block.previous)))
    return <EmptyState title={`Sin actividad en ${block.label}`} message={`La cuenta ${block.accountId ?? ''} no registró datos en el periodo seleccionado.`} />;
  return null;
}
