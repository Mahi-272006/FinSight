import { forwardRef } from 'react'
import {
  Activity,
  Bookmark,
  Building2,
  ChartNoAxesCombined,
  FileText,
  LayoutDashboard,
  SearchCheck,
  Scale,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'

const navigation = [
  { label: 'Overview', to: '/', icon: LayoutDashboard, end: true },
  { label: 'Companies', to: '/companies', icon: Building2 },
  { label: 'Anomalies', to: '/anomalies', icon: Activity },
  { label: 'Investment Thesis', to: '/thesis', icon: Scale },
  { label: 'AI Research', to: '/research', icon: SearchCheck },
  { label: 'Reports', to: '/reports', icon: FileText },
  { label: 'Watchlist', to: '/watchlist', icon: Bookmark },
]

const Sidebar = forwardRef(function Sidebar({ isOpen, onNavigate }, ref) {
  return (
    <aside ref={ref} id="primary-sidebar" className={`sidebar ${isOpen ? 'sidebar--open' : ''}`} aria-label="Primary navigation">
      <NavLink to="/" className="brand" onClick={onNavigate} aria-label="FinSight overview">
        <span className="brand__symbol" aria-hidden="true"><ChartNoAxesCombined size={19} strokeWidth={1.7} /></span>
        <span className="brand__word">FinSight<span className="brand__period">.</span></span>
      </NavLink>

      <div className="sidebar__rule" />
      <p className="sidebar__caption">WORKSPACE</p>
      <nav className="primary-nav">
        {navigation.map(({ label, to, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) => `nav-link${isActive ? ' nav-link--active' : ''}`}
          >
            <Icon size={17} strokeWidth={1.75} aria-hidden="true" />
            <span>{label}</span>
            {label === 'Anomalies' && <span className="nav-link__hint" aria-hidden="true">↗</span>}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar__bottom">
        <div className="sidebar-note">
          <span className="sidebar-note__mark" aria-hidden="true">§</span>
          <p>Research grounded in reported evidence.</p>
        </div>
        <div className="sidebar__footer">
          <span className="status-dot" aria-hidden="true" />
          <span>Local workspace</span>
          <span className="sidebar__version">M10</span>
        </div>
      </div>
    </aside>
  )
})

export default Sidebar
