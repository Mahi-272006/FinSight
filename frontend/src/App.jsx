import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell.jsx'
import OverviewPage from './pages/OverviewPage.jsx'
import SectionPage from './pages/SectionPage.jsx'
import CompaniesPage from './pages/CompaniesPage.jsx'
import AnomaliesPage from './pages/AnomaliesPage.jsx'
import ThesisPage from './pages/ThesisPage.jsx'
import ResearchPage from './pages/ResearchPage.jsx'
import ReportsPage from './pages/ReportsPage.jsx'
import WatchlistPage from './pages/WatchlistPage.jsx'
import './App.css'

const sections = [
  { path: 'companies', title: 'Companies', description: 'Company profiles and reported financial evidence.' },
  { path: 'anomalies', title: 'Anomalies', description: 'Deterministic signals that call for closer investigation.' },
  { path: 'thesis', title: 'Investment Thesis', description: 'Measurable commitments and their assessment history.' },
  { path: 'research', title: 'AI Research', description: 'Evidence-led research sessions with source-aware answers.' },
  { path: 'reports', title: 'Reports', description: 'Saved research results, preserved as generated.' },
  { path: 'watchlist', title: 'Watchlist', description: 'The companies you are keeping under review.' },
]

// Pages connected to real API data; every other section renders its
// honest foundation shell until its own stage lands.
const pageBySection = {
  companies: CompaniesPage,
  anomalies: AnomaliesPage,
  thesis: ThesisPage,
  research: ResearchPage,
  reports: ReportsPage,
  watchlist: WatchlistPage,
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          {sections.map((section) => {
            const Page = pageBySection[section.path]
            return (
              <Route
                key={section.path}
                path={section.path}
                element={Page ? <Page /> : <SectionPage {...section} />}
              />
            )
          })}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
