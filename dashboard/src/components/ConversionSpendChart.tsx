import { useEffect, useId, useRef, useState } from 'react';
import { formatDate } from '../lib/dates';
import { formatValue } from '../lib/format';
import type { FormatOptions } from '../lib/format';

export interface ConversionSpendDay {
  date: string;
  googleConversions: number | null;
  metaConversions: number | null;
  conversionsTotal: number | null;
  googleSpend: number | null;
  metaSpend: number | null;
  totalSpend: number | null;
}

type Platform = 'google_ads' | 'meta_ads';
interface Props {
  days: ConversionSpendDay[];
  platform: 'all' | Platform;
  googleCurrency: string | null;
  metaCurrency: string | null;
  mixedCurrency: boolean;
  partial: boolean;
  fmt: FormatOptions;
}

const LABEL: Record<Platform, string> = { google_ads: 'Google Ads', meta_ads: 'Meta Ads' };
const COLOR: Record<Platform, string> = { google_ads: 'var(--series-gads)', meta_ads: 'var(--series-meta)' };

function moneyAxis(value: number, currency: string, locale: string): string {
  return new Intl.NumberFormat(locale, { style: 'currency', currency, notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

function conversionAxis(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value);
}

function DualAxisChart({ days, platforms, currency, fmt }: { days: ConversionSpendDay[]; platforms: Platform[]; currency: string; fmt: FormatOptions }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(700);
  const titleId = useId();
  const height = 290;
  const pad = { top: 18, right: 76, bottom: 32, left: 72 };
  useEffect(() => {
    const el = wrap.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(300, Math.floor(entry.contentRect.width))));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const maxConversions = Math.max(1, ...days.flatMap((day) => platforms.map((platform) => {
    const value = platform === 'google_ads' ? day.googleConversions : day.metaConversions;
    return value ?? 0;
  })));
  const spendFor = (day: ConversionSpendDay, platform: Platform) => platform === 'google_ads' ? day.googleSpend : day.metaSpend;
  const maxSpend = Math.max(1, ...days.flatMap((day) => platforms.map((platform) => spendFor(day, platform) ?? 0)));
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const x = (index: number) => pad.left + (days.length <= 1 ? innerWidth / 2 : index / (days.length - 1) * innerWidth);
  const yConversion = (value: number) => pad.top + innerHeight - value / maxConversions * innerHeight;
  const ySpend = (value: number) => pad.top + innerHeight - value / maxSpend * innerHeight;
  const labelEvery = Math.max(1, Math.ceil(days.length / (width < 520 ? 4 : 8)));
  const barGroupWidth = Math.min(24, Math.max(5, innerWidth / Math.max(1, days.length) * 0.62));
  const barWidth = barGroupWidth / platforms.length;

  return <section className="card conversion-spend-chart" aria-labelledby={titleId}>
    <h3 id={titleId}>Conversiones por día y gasto por día</h3>
    <ul className="legend conversion-spend-legend" aria-label="Leyenda">
      {platforms.map((platform) => <li key={`${platform}-conversion`}><span className="conversion-bar-key" style={{ background: COLOR[platform] }} />Conversiones · {LABEL[platform]}</li>)}
      {platforms.map((platform) => <li key={`${platform}-spend`}><span className="legend-line" style={{ background: COLOR[platform] }} />Gasto · {LABEL[platform]} ({currency})</li>)}
    </ul>
    <div className="conversion-spend-svg-wrap" ref={wrap}>
      <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Barras de conversiones y líneas de gasto en ${currency}`}>
        {[0, .25, .5, .75, 1].map((fraction) => {
          const value = maxConversions * fraction;
          const money = maxSpend * fraction;
          const yy = yConversion(value);
          return <g key={fraction}>
            <line className="chart-grid" x1={pad.left} x2={width - pad.right} y1={yy} y2={yy} />
            <text className="chart-axis" x={pad.left - 7} y={yy} dy=".32em" textAnchor="end">{conversionAxis(value, fmt.locale)}</text>
            <text className="chart-axis" x={width - pad.right + 7} y={yy} dy=".32em">{moneyAxis(money, currency, fmt.locale)}</text>
          </g>;
        })}
        {days.map((day, index) => {
          const groupStart = x(index) - barGroupWidth / 2;
          return <g key={day.date}>
            {platforms.map((platform, platformIndex) => {
              const value = platform === 'google_ads' ? day.googleConversions : day.metaConversions;
              return value == null ? null : <rect key={platform} x={groupStart + platformIndex * barWidth} y={yConversion(value)} width={Math.max(2, barWidth - 2)} height={Math.max(0, pad.top + innerHeight - yConversion(value))} rx="2" fill={COLOR[platform]} opacity=".8" />;
            })}
            {(index % labelEvery === 0 || index === days.length - 1) && <text className="chart-axis" x={x(index)} y={height - 8} textAnchor={index === 0 ? 'start' : index === days.length - 1 ? 'end' : 'middle'}>{formatDate(day.date).replace(/ \d{4}$/, '')}</text>}
          </g>;
        })}
        {platforms.map((platform) => {
          const d = days.reduce((path, day, index) => {
            const value = spendFor(day, platform);
            return value == null ? path : `${path}${path ? 'L' : 'M'}${x(index).toFixed(1)},${ySpend(value).toFixed(1)}`;
          }, '');
          return d ? <path key={platform} d={d} fill="none" stroke={COLOR[platform]} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" /> : null;
        })}
      </svg>
    </div>
  </section>;
}

export function ConversionSpendChart({ days, platform, googleCurrency, metaCurrency, mixedCurrency, partial, fmt }: Props) {
  const [showTable, setShowTable] = useState(false);
  const platforms: Platform[] = platform === 'all' ? ['google_ads', 'meta_ads'] : [platform];
  const currencyFor = (item: Platform) => item === 'google_ads' ? googleCurrency : metaCurrency;
  const sharedCurrency = !mixedCurrency && new Set(platforms.map(currencyFor).filter(Boolean)).size <= 1;
  const charts = sharedCurrency
    ? [{ platforms, currency: currencyFor(platforms[0]) ?? fmt.currency }]
    : platforms.map((item) => ({ platforms: [item], currency: currencyFor(item) ?? fmt.currency }));
  const formatNullable = (value: number | null, currency?: string | null) => value == null ? '—'
    : formatValue(value, currency ? 'currency' : 'number', currency ? { ...fmt, currency } : fmt);
  const tablePlatforms = platform === 'all' ? ['google_ads', 'meta_ads'] as const : [platform] as const;
  const hasChartValues = days.some((day) => platforms.some((item) =>
    (item === 'google_ads' ? day.googleConversions != null || day.googleSpend != null : day.metaConversions != null || day.metaSpend != null)));
  return <section className="report-section conversion-spend-section">
    <header className="conversion-spend-section-head">
      <h2>CONVERSIONES VS GASTO</h2>
      <p className="muted small">Barras: conversiones. Línea: gasto. Usa ejes independientes para no comparar escalas distintas.</p>
    </header>
    {mixedCurrency && <p className="note-inline small">Las plataformas utilizan monedas distintas. Se muestran en gráficas separadas; no se calcula gasto total ni CPA combinado.</p>}
    {showTable ? <div className="table-scroll conversion-spend-table"><table className="data-table">
      <thead><tr><th>Fecha</th>{tablePlatforms.includes('google_ads') && <th className="num">Conversiones Google</th>}{tablePlatforms.includes('meta_ads') && <th className="num">Conversiones Meta</th>}<th className="num">Conversiones totales{partial ? ' · parcial' : ''}</th>{tablePlatforms.includes('google_ads') && <th className="num">Gasto Google</th>}{tablePlatforms.includes('meta_ads') && <th className="num">Gasto Meta</th>}<th className="num">Gasto comparable{partial ? ' · parcial' : ''}</th></tr></thead>
      <tbody>{days.map((day) => <tr key={day.date}><td>{formatDate(day.date)}</td>{tablePlatforms.includes('google_ads') && <td className="num">{formatNullable(day.googleConversions)}</td>}{tablePlatforms.includes('meta_ads') && <td className="num">{formatNullable(day.metaConversions)}</td>}<td className="num">{formatNullable(day.conversionsTotal)}</td>{tablePlatforms.includes('google_ads') && <td className="num">{formatNullable(day.googleSpend, googleCurrency)}</td>}{tablePlatforms.includes('meta_ads') && <td className="num">{formatNullable(day.metaSpend, metaCurrency)}</td>}<td className="num">{formatNullable(mixedCurrency ? null : day.totalSpend, mixedCurrency ? null : (googleCurrency ?? metaCurrency))}</td></tr>)}</tbody>
    </table></div> : !hasChartValues ? <p className="card note">No hay datos diarios disponibles para las plataformas y filtros seleccionados.</p> : <div className={charts.length > 1 ? 'conversion-spend-chart-grid' : undefined}>{charts.map((chart, index) => <DualAxisChart key={`${chart.platforms.join('-')}-${chart.currency}-${index}`} days={days} platforms={chart.platforms} currency={chart.currency} fmt={fmt} />)}</div>}
    <button type="button" className="btn btn-ghost btn-sm conversion-spend-table-toggle" aria-pressed={showTable} onClick={() => setShowTable((value) => !value)}>{showTable ? 'Ver gráfica' : 'Ver tabla'}</button>
  </section>;
}
