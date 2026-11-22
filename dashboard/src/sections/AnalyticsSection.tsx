import { useState } from 'react';
import { DataTable, type Column } from '../components/DataTable';
import { LineChart } from '../components/LineChart';
import { KpiSkeleton, TableSkeleton } from '../components/States';
import type { ClientConfig } from '../config/clients';
import { METRICS, type MetricDef } from '../config/metrics';
import { formatRange } from '../lib/dates';
import { formatValue, type FormatOptions } from '../lib/format';
import type { Ga4Period, Ga4Property, Ga4Row, Ga4Warning } from '../types/dashboard';
import type { DateSelection } from '../types/report';
import { KpiRow, SectionTitle, blockStatusView, type BlockViewProps } from './common';

const BASE_KPIS = [METRICS.ga_sessions, METRICS.ga_engaged, METRICS.ga_engagement_rate, METRICS.ga_key_events, METRICS.ga_revenue];
/** Por propiedad: activeUsers es el dato nativo de GA4, sin salvedades. */
const KPIS = [METRICS.ga_users, ...BASE_KPIS];
/**
 * Consolidado: los usuarios activos de varias propiedades NO se pueden
 * deduplicar (un mismo usuario puede estar en dos), así que se suman y se
 * etiquetan explícitamente como suma.
 */
const KPIS_CONSOLIDATED = [METRICS.ga_users_sum, ...BASE_KPIS];

type Tab = 'consolidated' | 'by_property';

interface Props extends BlockViewProps<Ga4Period> {
  client: ClientConfig;
  selection: DateSelection;
  refreshing?: boolean;
}

function columns(first: string, totals: Ga4Period['totals']): Column<Ga4Row>[] {
  return [
    { key: 'name', label: first, value: (r) => r.name || '(not set)' },
    { key: 'users', label: 'Users', value: (r) => r.users, format: 'number' },
    { key: 'sessions', label: 'Sessions', value: (r) => r.sessions, format: 'number', total: totals.sessions },
    { key: 'engaged', label: 'Engaged Sessions', value: (r) => r.engaged_sessions, format: 'number', total: totals.engaged_sessions },
    { key: 'rate', label: 'Engagement Rate', value: (r) => r.engagement_rate, format: 'percent' },
    { key: 'key_events', label: 'Key Events', value: (r) => r.key_events, format: 'decimal', total: totals.key_events },
    { key: 'revenue', label: 'Revenue', value: (r) => r.revenue, format: 'currency', total: totals.revenue },
  ];
}

/** Avisos: propiedades caídas (con su property_id) y monedas mezcladas. */
function Warnings({ warnings }: { warnings: Ga4Warning[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="card state state-warn ga-warnings" role="status">
      <div className="state-icon" aria-hidden>
        !
      </div>
      <div>
        <h3>
          {warnings.length === 1 ? 'Un aviso en Google Analytics' : `${warnings.length} avisos en Google Analytics`}
        </h3>
        <p>Los datos mostrados corresponden a las propiedades que sí respondieron.</p>
        <ul className="ga-warning-list small">
          {warnings.map((w, i) => (
            <li key={`${w.propertyId ?? 'global'}-${i}`}>
              {w.propertyId && <strong>Propiedad {w.propertyId}: </strong>}
              {w.message}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Cuerpo común a "Consolidado" y a cada propiedad: KPIs, gráficas y tablas. */
function PeriodView({
  cur,
  prev,
  kpis,
  fmt,
  subtitle,
  compareLabel,
  footnote,
}: {
  cur: Ga4Period;
  prev: Ga4Period | null;
  kpis: MetricDef[];
  fmt: FormatOptions;
  subtitle: string;
  compareLabel?: string;
  footnote: string;
}) {
  const dates = cur.daily.map((d) => d.date);
  const chart = (title: string, key: 'sessions' | 'users' | 'key_events' | 'revenue', format: 'number' | 'decimal' | 'currency') => (
    <LineChart
      title={title}
      subtitle={subtitle}
      dates={dates}
      height={200}
      series={[{ key, label: title, color: 'var(--series-ga)', values: cur.daily.map((d) => d[key]) }]}
      format={(v) => formatValue(v, format, fmt)}
    />
  );
  return (
    <>
      <KpiRow metrics={kpis} current={cur.totals} previous={prev?.totals ?? null} fmt={fmt} compareLabel={compareLabel} />
      <div className="chart-grid-2">
        {chart('Sessions', 'sessions', 'number')}
        {chart('Users', 'users', 'number')}
        {chart('Key Events', 'key_events', 'decimal')}
        {chart('Revenue', 'revenue', 'currency')}
      </div>
      <DataTable
        title="Source / Medium"
        subtitle={subtitle}
        rows={cur.by_source_medium}
        columns={columns('Source / Medium', cur.totals)}
        rowKey={(r) => r.name}
        fmt={fmt}
        searchPlaceholder="Buscar source / medium…"
        initialSort={{ key: 'sessions', dir: 'desc' }}
      />
      <DataTable
        title="Campaign"
        subtitle={subtitle}
        rows={cur.by_campaign}
        columns={columns('Campaign', cur.totals)}
        rowKey={(r) => r.name}
        fmt={fmt}
        initialSort={{ key: 'sessions', dir: 'desc' }}
      />
      <DataTable
        title="Landing Page"
        subtitle={subtitle}
        rows={cur.by_landing_page}
        columns={columns('Landing Page', cur.totals)}
        rowKey={(r) => r.name}
        fmt={fmt}
        searchPlaceholder="Buscar landing page…"
        initialSort={{ key: 'sessions', dir: 'desc' }}
      />
      <p className="muted small">{footnote}</p>
    </>
  );
}

/** Vista "Por propiedad": una propiedad a la vez, con su propio selector. */
function ByProperty({
  properties,
  subtitle,
  compareLabel,
  clientCurrency,
  locale,
}: {
  properties: Ga4Property[];
  subtitle: string;
  compareLabel?: string;
  clientCurrency: string;
  locale: string;
}) {
  const [selected, setSelected] = useState(properties.find((p) => p.status === 'ok')?.propertyId ?? properties[0]?.propertyId ?? '');
  const prop = properties.find((p) => p.propertyId === selected) ?? properties[0];
  if (!prop) return null;
  const label = (p: Ga4Property) => `${p.propertyName ?? `Propiedad ${p.propertyId}`} — ${p.propertyId}${p.currency ? ` · ${p.currency}` : ''}`;
  // Cada propiedad se formatea con SU moneda, no con la del cliente.
  const propFmt: FormatOptions = { currency: prop.currency ?? clientCurrency, locale };

  return (
    <>
      <div className="ga-property-bar">
        <label className="small muted" htmlFor="ga-property">
          Propiedad
        </label>
        <select id="ga-property" className="input" value={prop.propertyId} onChange={(e) => setSelected(e.target.value)}>
          {properties.map((p) => (
            <option key={p.propertyId} value={p.propertyId}>
              {label(p)}
              {p.status !== 'ok' ? ' — no disponible' : ''}
            </option>
          ))}
        </select>
        <span className="muted small">
          {properties.length} propiedad{properties.length === 1 ? '' : 'es'} seleccionada{properties.length === 1 ? '' : 's'}
        </span>
      </div>
      {prop.status !== 'ok' || !prop.current ? (
        <Warnings
          warnings={[{ propertyId: prop.propertyId, status: prop.status, message: prop.detail ?? prop.message ?? 'No disponible' }]}
        />
      ) : (
        <PeriodView
          key={prop.propertyId}
          cur={prop.current}
          prev={prop.previous}
          kpis={KPIS}
          fmt={propFmt}
          subtitle={subtitle}
          compareLabel={compareLabel}
          footnote="Top 50 por sesiones en cada tabla. Users (usuarios activos) no es aditivo entre filas: un usuario puede llegar por varias fuentes."
        />
      )}
    </>
  );
}

export function AnalyticsSection({ client, selection, block, loading, onRetry, refreshing }: Props) {
  const [tab, setTab] = useState<Tab>('consolidated');
  const fmt: FormatOptions = { currency: block?.currency ?? client.currency, locale: client.locale };
  const compareLabel = selection.compareRange ? formatRange(selection.compareRange) : undefined;
  const subtitle = formatRange(selection.range, true);

  const properties = block?.properties ?? [];
  const propertyIds = block?.propertyIds ?? (block?.accountId ? [block.accountId] : []);
  const multi = propertyIds.length > 1;
  const warnings = block?.warnings ?? [];

  let body = blockStatusView(
    { block, loading, onRetry },
    {
      clientName: client.name,
      isEmpty: (p) => p.totals.sessions === 0 && p.daily.length === 0,
      skeleton: (
        <>
          <KpiSkeleton count={KPIS.length} />
          <TableSkeleton />
        </>
      ),
    },
  );

  if (!body && block?.current) {
    // Con una sola propiedad el consolidado ES la propiedad: no se ofrecen pestañas.
    const showTabs = multi && properties.length > 0;
    const showByProperty = showTabs && tab === 'by_property';
    const count = block.current.propertiesCount ?? propertyIds.length;
    body = (
      <div className={refreshing ? 'refreshing' : undefined}>
        {showTabs && (
          <div className="ga-tabs" role="tablist" aria-label="Vista de Google Analytics">
            {(
              [
                ['consolidated', 'Consolidado'],
                ['by_property', 'Por propiedad'],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                className={`ga-tab${tab === id ? ' active' : ''}`}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        <Warnings warnings={showByProperty ? warnings.filter((w) => w.propertyId === null) : warnings} />

        {showByProperty ? (
          <ByProperty
            properties={properties}
            subtitle={subtitle}
            compareLabel={compareLabel}
            clientCurrency={client.currency}
            locale={client.locale}
          />
        ) : (
          <PeriodView
            cur={block.current}
            prev={block.previous}
            kpis={multi ? KPIS_CONSOLIDATED : KPIS}
            fmt={fmt}
            subtitle={subtitle}
            compareLabel={compareLabel}
            footnote={
              multi
                ? `Datos combinados de ${count} propiedad${count === 1 ? '' : 'es'} GA4. Sessions, Engaged Sessions, Key Events y Revenue se suman; Engagement Rate se recalcula como engaged / sessions del total. Los usuarios activos se suman y NO están deduplicados entre propiedades. Top 50 por sesiones en cada tabla.`
                : 'Top 50 por sesiones en cada tabla. Users (usuarios activos) no es aditivo entre filas: un usuario puede llegar por varias fuentes.'
            }
          />
        )}
      </div>
    );
  }

  return (
    <section className="report-section">
      <SectionTitle title="Google Analytics 4" tag="tag-ga">
        {propertyIds.length > 0 && (
          <span className="muted small">
            {propertyIds.length === 1 ? `Propiedad ${propertyIds[0]}` : `${propertyIds.length} propiedades seleccionadas`}
          </span>
        )}
      </SectionTitle>
      {body}
    </section>
  );
}
