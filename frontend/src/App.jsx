import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell.jsx'
import OverviewPage from './pages/OverviewPage.jsx'
import SectionPage from './pages/SectionPage.jsx'
import './App.css'

const sections = [
  { path: 'companies', title: 'Companies', description: 'Company profiles and reported financial evidence.' },
  { path: 'anomalies', title: 'Anomalies', description: 'Deterministic signals that call for closer investigation.' },
  { path: 'thesis', title: 'Investment Thesis', description: 'Measurable commitments and their assessment history.' },
  { path: 'research', title: 'AI Research', description: 'Evidence-led research sessions with source-aware answers.' },
  { path: 'reports', title: 'Reports', description: 'Saved research results, preserved as generated.' },
  { path: 'watchlist', title: 'Watchlist', description: 'The companies you are keeping under review.' },
]

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          {sections.map((section) => (
            <Route
              key={section.path}
              path={section.path}
              element={<SectionPage {...section} />}
            />
          ))}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
