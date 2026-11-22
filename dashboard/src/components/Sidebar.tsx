export type PageId = 'overview' | 'google-ads' | 'meta-ads' | 'spotify-ads' | 'pinterest-ads' | 'analytics' | 'sales' | 'settings-clients';

interface NavItem {
  id: PageId;
  label: string;
  icon: string;
  disabled?: boolean;
  badge?: string;
}

const NAV: NavItem[] = [
  { id: 'overview', label: 'Overview', icon: '◧' },
  { id: 'google-ads', label: 'Google Ads', icon: '◆' },
  { id: 'meta-ads', label: 'Meta Ads', icon: '●' },
  { id: 'spotify-ads', label: 'Spotify Ads', icon: '♫' },
  { id: 'pinterest-ads', label: 'Pinterest Ads', icon: 'P' },
  { id: 'analytics', label: 'Google Analytics', icon: '▲' },
];
const SALES_NAV: NavItem = { id: 'sales', label: 'Ventas', icon: '▥' };

const SETTINGS_NAV: NavItem[] = [{ id: 'settings-clients', label: 'Clientes', icon: '⚙' }];

interface Props {
  page: PageId;
  onNavigate: (p: PageId) => void;
  open: boolean;
  onClose: () => void;
  footer: string;
  showSales: boolean;
  showSpotify?: boolean;
}

export function Sidebar({ page, onNavigate, open, onClose, footer, showSales, showSpotify = false }: Props) {
  const item = (n: NavItem) => (
    <button
      key={n.id}
      type="button"
      className={page === n.id ? 'nav-item active' : 'nav-item'}
      disabled={n.disabled}
      aria-current={page === n.id ? 'page' : undefined}
      onClick={() => {
        onNavigate(n.id);
        onClose();
      }}
    >
      <span className="nav-icon" aria-hidden>
        {n.icon}
      </span>
      {n.label}
      {n.badge && <span className="badge">{n.badge}</span>}
    </button>
  );

  return (
    <>
      <div className={open ? 'scrim show' : 'scrim'} onClick={onClose} aria-hidden />
      <aside className={open ? 'sidebar open' : 'sidebar'} aria-label="Navegación del reporte">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            MP
          </span>
          <span>
            <strong>Performance</strong>
            <span className="muted small block">Cloud Reports</span>
          </span>
        </div>
        <nav>
          <div className="nav-title">Páginas del reporte</div>
          {NAV.filter((navItem) => navItem.id !== 'spotify-ads' || showSpotify).map(item)}
          {showSales && item(SALES_NAV)}
          <div className="nav-title nav-title-gap">Settings</div>
          {SETTINGS_NAV.map(item)}
        </nav>
        <div className="sidebar-foot muted small">
          {footer}
        </div>
      </aside>
    </>
  );
}
