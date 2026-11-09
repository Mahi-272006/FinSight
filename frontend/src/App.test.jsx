import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

vi.mock('./pages/OverviewPage.jsx', () => ({ default: () => <div data-testid="overview-page" /> }))
vi.mock('./pages/CompaniesPage.jsx', () => ({ default: () => <div data-testid="companies-page" /> }))
vi.mock('./pages/AnomaliesPage.jsx', () => ({ default: () => <div data-testid="anomalies-page" /> }))
vi.mock('./pages/ThesisPage.jsx', () => ({ default: () => <div data-testid="thesis-page" /> }))
vi.mock('./pages/ResearchPage.jsx', () => ({ default: () => <div data-testid="research-page" /> }))
vi.mock('./pages/ReportsPage.jsx', () => ({ default: () => <div data-testid="reports-page" /> }))
vi.mock('./pages/SectionPage.jsx', () => ({ default: ({ title }) => <div data-testid="section-page">{title}</div> }))

import App from './App.jsx'

describe('App routing', () => {
  afterEach(() => {
    cleanup()
    window.history.pushState({}, '', '/')
  })

  it('renders the overview at the index route', () => {
    window.history.pushState({}, '', '/')
    render(<App />)
    expect(screen.getByTestId('overview-page')).toBeTruthy()
    expect(screen.queryByTestId('section-page')).toBeNull()
  })

  it('renders the Companies page for /companies', () => {
    window.history.pushState({}, '', '/companies')
    render(<App />)
    expect(screen.getByTestId('companies-page')).toBeTruthy()
    expect(screen.queryByTestId('section-page')).toBeNull()
  })

  it('renders the Anomalies page for /anomalies', () => {
    window.history.pushState({}, '', '/anomalies')
    render(<App />)
    expect(screen.getByTestId('anomalies-page')).toBeTruthy()
    expect(screen.queryByTestId('section-page')).toBeNull()
  })

  it('renders the Thesis page for /thesis', () => {
    window.history.pushState({}, '', '/thesis')
    render(<App />)
    expect(screen.getByTestId('thesis-page')).toBeTruthy()
    expect(screen.queryByTestId('section-page')).toBeNull()
  })

  it('renders the Research page for /research', () => {
    window.history.pushState({}, '', '/research')
    render(<App />)
    expect(screen.getByTestId('research-page')).toBeTruthy()
    expect(screen.queryByTestId('section-page')).toBeNull()
  })

  it('renders the Reports page for /reports', () => {
    window.history.pushState({}, '', '/reports')
    render(<App />)
    expect(screen.getByTestId('reports-page')).toBeTruthy()
    expect(screen.queryByTestId('section-page')).toBeNull()
  })

  it('renders a foundation shell for the watchlist route', () => {
    window.history.pushState({}, '', '/watchlist')
    render(<App />)
    expect(screen.getByTestId('section-page').textContent).toBe('Watchlist')
  })

  it('redirects unknown routes to the overview', () => {
    window.history.pushState({}, '', '/does-not-exist')
    render(<App />)
    expect(screen.getByTestId('overview-page')).toBeTruthy()
  })
})
