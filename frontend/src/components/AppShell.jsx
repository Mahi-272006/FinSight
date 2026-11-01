import { useEffect, useRef, useState } from 'react'
import { Menu, Search, X } from 'lucide-react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'

const pageTitles = {
  '/': 'Overview',
  '/companies': 'Companies',
  '/anomalies': 'Anomalies',
  '/thesis': 'Investment Thesis',
  '/research': 'AI Research',
  '/reports': 'Reports',
  '/watchlist': 'Watchlist',
}

function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const sidebarRef = useRef(null)
  const menuToggleRef = useRef(null)
  const wasSidebarOpen = useRef(false)
  const location = useLocation()
  const title = pageTitles[location.pathname] || 'Overview'

  useEffect(() => {
    if (sidebarOpen) {
      sidebarRef.current?.querySelector('.primary-nav a')?.focus()
    } else if (wasSidebarOpen.current) {
      menuToggleRef.current?.focus()
    }
    wasSidebarOpen.current = sidebarOpen
  }, [sidebarOpen])

  useEffect(() => {
    if (!sidebarOpen) return undefined

    function handleDrawerKeyDown(event) {
      if (event.key === 'Escape') {
        event.preventDefault()
        setSidebarOpen(false)
        return
      }

      if (event.key !== 'Tab') return
      const links = sidebarRef.current?.querySelectorAll('a[href]')
      if (!links?.length) return

      const first = links[0]
      const last = links[links.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleDrawerKeyDown)
    return () => document.removeEventListener('keydown', handleDrawerKeyDown)
  }, [sidebarOpen])

  return (
    <div className="app-frame">
      {sidebarOpen && (
        <button
          className="sidebar-scrim"
          type="button"
          aria-label="Close navigation menu"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <Sidebar
        ref={sidebarRef}
        isOpen={sidebarOpen}
        onNavigate={() => setSidebarOpen(false)}
      />
      <div className="workspace">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            type="button"
            aria-label={sidebarOpen ? 'Close navigation menu' : 'Open navigation menu'}
            aria-expanded={sidebarOpen}
            aria-controls="primary-sidebar"
            ref={menuToggleRef}
            onClick={() => setSidebarOpen((open) => !open)}
          >
            {sidebarOpen ? <X size={19} /> : <Menu size={19} />}
          </button>
          <div className="topbar__section">
            <span className="topbar__label">Research workspace</span>
            <span className="topbar__divider" aria-hidden="true">/</span>
            <span className="topbar__current">{title}</span>
          </div>
          <div className="topbar__right">
            <span className="topbar__edition">Evidence, considered.</span>
            <button className="icon-button search-shortcut" type="button" aria-label="Search is not available yet" disabled>
              <Search size={17} />
            </button>
          </div>
        </header>
        <main id="main-content" className="main-content">
          <Outlet />
          <footer className="page-footer">
            <span>FINSIGHT RESEARCH PLATFORM</span>
            <span>Evidence first. Always.</span>
          </footer>
        </main>
      </div>
    </div>
  )
}

export default AppShell
