import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api/overview.js', () => ({
  getCompanies: vi.fn(),
  getCompanyAnomalies: vi.fn(),
  getReports: vi.fn(),
  getTheses: vi.fn(),
  getWatchlist: vi.fn(),
}))

import {
  getCompanies,
  getCompanyAnomalies,
  getReports,
  getTheses,
  getWatchlist,
} from '../api/overview.js'
import OverviewPage from './OverviewPage.jsx'

const now = '2026-10-31T12:00:00Z'
const emptyPage = (limit = 20) => ({ items: [], total: 0, limit, offset: 0 })

function renderOverview() {
  return render(<MemoryRouter><OverviewPage /></MemoryRouter>)
}

function configureEmptyResponses() {
  getCompanies.mockResolvedValue(emptyPage(20))
  getWatchlist.mockResolvedValue(emptyPage(8))
  getReports.mockResolvedValue(emptyPage(5))
  getTheses.mockResolvedValue(emptyPage(5))
  getCompanyAnomalies.mockResolvedValue(emptyPage(5))
}

describe('Overview page states', () => {
  beforeEach(configureEmptyResponses)
  afterEach(cleanup)

  it('shows loading states while parallel requests are pending and aborts on unmount', async () => {
    getCompanies.mockImplementation(() => new Promise(() => {}))
    getWatchlist.mockImplementation(() => new Promise(() => {}))
    getReports.mockImplementation(() => new Promise(() => {}))
    getTheses.mockImplementation(() => new Promise(() => {}))
    const { unmount } = renderOverview()

    expect(screen.getAllByRole('status').length).toBeGreaterThan(0)
    await waitFor(() => expect(getCompanies).toHaveBeenCalled())
    const signal = getCompanies.mock.calls[0][0].signal
    unmount()
    expect(signal.aborted).toBe(true)
  })

  it('renders valid empty API results as empty states and zero totals', async () => {
    renderOverview()

    expect(await screen.findByText('No saved research yet')).toBeTruthy()
    expect(screen.getByText('Your watchlist is clear')).toBeTruthy()
    expect(screen.getByText('No investment theses yet')).toBeTruthy()
    expect(screen.getByText('No companies in the database')).toBeTruthy()
    expect(screen.getAllByText('0').length).toBe(3)
    expect(getCompanyAnomalies).not.toHaveBeenCalled()
  })

  it('renders API failures as errors without showing backend detail strings', async () => {
    getReports.mockRejectedValue({
      response: { status: 500, data: { detail: 'postgres://internal-secret' } },
    })
    renderOverview()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('backend encountered a problem')
    expect(alert.textContent).not.toContain('postgres')
    expect(screen.getByText('Unavailable')).toBeTruthy()
  })

  it('renders actual company, watchlist, report, thesis, and signal values', async () => {
    getCompanies.mockResolvedValue({
      items: [{
        cik: 789019,
        ticker: 'MSFT',
        name: 'Microsoft Corporation',
        fiscal_year_end_month: 6,
        created_at: now,
        updated_at: now,
      }],
      total: 1,
      limit: 20,
      offset: 0,
    })
    getWatchlist.mockResolvedValue({
      items: [{ cik: 789019, ticker: 'MSFT', created_at: now }],
      total: 1,
      limit: 8,
      offset: 0,
    })
    getReports.mockResolvedValue({
      items: [{
        id: 5,
        cik: 789019,
        ticker: 'MSFT',
        question: 'How durable is the reported growth?',
        thread_id: 'thread-5',
        created_at: now,
      }],
      total: 1,
      limit: 5,
      offset: 0,
    })
    getTheses.mockResolvedValue({
      items: [{
        id: 8,
        ticker: 'MSFT',
        statement: 'Growth remains durable.',
        horizon: 'Long term',
        status: 'active',
        created_at: now,
      }],
      total: 1,
      limit: 5,
      offset: 0,
    })
    getCompanyAnomalies.mockResolvedValue({
      items: [{
        id: 3,
        cik: 789019,
        ticker: 'MSFT',
        signal_type: 'growth_deceleration',
        severity: 'elevated',
        fiscal_year: 2025,
        fiscal_period: 'FY',
        period_type: 'annual',
        headline: 'Reported growth decelerated.',
        explanation: 'Stored explanation.',
        evidence: { values: [1, 2] },
        source_accessions: ['0000789019-25-000001'],
        detected_at: now,
      }],
      total: 1,
      limit: 5,
      offset: 0,
    })

    renderOverview()

    expect(await screen.findByText('Microsoft Corporation')).toBeTruthy()
    expect(screen.getByText('How durable is the reported growth?')).toBeTruthy()
    expect(screen.getByText('Reported growth decelerated.')).toBeTruthy()
    expect(screen.getByText('Growth remains durable.')).toBeTruthy()
    expect(screen.getAllByText('MSFT').length).toBeGreaterThan(0)
  })
})
