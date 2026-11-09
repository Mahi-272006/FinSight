import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/overview.js', () => ({ getCompanies: vi.fn() }))
vi.mock('../api/anomalies.js', () => ({ getAnomalySignals: vi.fn() }))

import { getCompanies } from '../api/overview.js'
import { getAnomalySignals } from '../api/anomalies.js'
import AnomaliesPage from './AnomaliesPage.jsx'

const timestamp = '2026-01-01T00:00:00Z'
const companies = [
  { cik: 789019, ticker: 'MSFT', name: 'Microsoft Corporation', fiscal_year_end_month: 6, created_at: timestamp, updated_at: timestamp },
  { cik: 320193, ticker: 'AAPL', name: 'Apple Inc.', fiscal_year_end_month: 9, created_at: timestamp, updated_at: timestamp },
]
const signal = {
  id: 5,
  cik: 789019,
  ticker: 'MSFT',
  signal_type: 'earnings_cashflow_divergence',
  severity: 'elevated',
  fiscal_year: 2025,
  fiscal_period: 'FY',
  period_type: 'annual',
  headline: 'Net income grew 12.0% while operating cash flow grew 3.0% (gap 9.0pp)',
  explanation: 'Reported profit is growing faster than the cash the business generates. This is a prompt to review the cash-flow statement -- NOT a finding of misstatement.',
  evidence: {
    net_income_growth: 0.12,
    operating_cash_flow_growth: 0.03,
    divergence: 0.09,
    threshold_elevated: 0.05,
    threshold_high: 0.1,
    formula: 'net_income_growth_yoy - operating_cash_flow_growth_yoy',
  },
  source_accessions: ['0000789019-25-000001'],
  detected_at: timestamp,
}
const page = (items, total = items.length, limit = 20, offset = 0) => ({ items, total, limit, offset })

function setupSuccess({ signals = [signal] } = {}) {
  getCompanies.mockResolvedValue(page(companies, companies.length, 100, 0))
  getAnomalySignals.mockResolvedValue(page(signals))
}

describe('Anomalies page', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('renders persisted signals with severity, period, explanation, evidence, and accessions', async () => {
    setupSuccess()
    render(<AnomaliesPage />)

    expect(await screen.findByText(/Microsoft Corporation/)).toBeTruthy()
    expect(await screen.findByText(/Net income grew 12\.0%/)).toBeTruthy()
    expect(screen.getByText('elevated')).toBeTruthy()
    expect(screen.getByText('earnings cashflow divergence', { selector: '.signal-row__type' })).toBeTruthy()
    expect(screen.getByText('FY 2025 · annual')).toBeTruthy()
    expect(screen.getByText(/NOT a finding of misstatement/)).toBeTruthy()
    expect(screen.getByText(/threshold_elevated/)).toBeTruthy()
    expect(screen.getByText(/0000789019-25-000001/)).toBeTruthy()
    expect(screen.getByText('Jan 1, 2026')).toBeTruthy()
  })

  it('shows an honest empty state when no signals are recorded for the company', async () => {
    setupSuccess({ signals: [] })
    render(<AnomaliesPage />)

    expect(await screen.findByText('No investigation signals recorded')).toBeTruthy()
    expect(screen.queryByText('No signals of this type')).toBeNull()
  })

  it('shows a friendly filtered-empty state when the signal type matches no rows', async () => {
    setupSuccess()
    getAnomalySignals.mockRejectedValue({ response: { status: 404, data: { detail: 'No investigation signals found for this signal type.' } } })
    render(<AnomaliesPage />)

    await screen.findByText(/Microsoft Corporation/)
    fireEvent.change(screen.getByRole('combobox', { name: 'Signal type' }), { target: { value: 'growth_deceleration' } })

    expect(await screen.findByText('No signals of this type')).toBeTruthy()
    expect(screen.getByText(/No growth deceleration signals are recorded for MSFT\./)).toBeTruthy()
    expect(screen.queryByText('The requested records are not available.')).toBeNull()
  })

  it('treats an unfiltered 404 as an API error and recovers through retry', async () => {
    setupSuccess()
    getAnomalySignals
      .mockRejectedValueOnce({ response: { status: 404, data: { detail: 'Company not found.' } } })
      .mockResolvedValueOnce(page([signal]))
    render(<AnomaliesPage />)

    expect(await screen.findByText('The requested records are not available.')).toBeTruthy()
    expect(screen.queryByText('No signals of this type')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(/Net income grew 12\.0%/)).toBeTruthy()
  })

  it('shows safe errors when the signals endpoint fails', async () => {
    setupSuccess()
    getAnomalySignals.mockRejectedValue({ response: { status: 500, data: { detail: 'database DSN secret' } } })
    render(<AnomaliesPage />)

    expect(await screen.findByText('The backend encountered a problem. Please try again later.')).toBeTruthy()
    expect(screen.queryByText(/DSN secret/)).toBeNull()
  })

  it('requests the selected signal type and resets pagination when the filter changes', async () => {
    setupSuccess()
    getAnomalySignals.mockResolvedValue(page([signal], 45))
    render(<AnomaliesPage />)

    await screen.findByText(/Net income grew 12\.0%/)
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(getAnomalySignals).toHaveBeenLastCalledWith(
      'MSFT',
      expect.objectContaining({ offset: 20 }),
    ))

    fireEvent.change(screen.getByRole('combobox', { name: 'Signal type' }), { target: { value: 'margin_compression_streak' } })
    await waitFor(() => expect(getAnomalySignals).toHaveBeenLastCalledWith(
      'MSFT',
      expect.objectContaining({ signalType: 'margin_compression_streak', offset: 0 }),
    ))
  })

  it('switches selected company and cancels the prior request on unmount', async () => {
    setupSuccess()
    let firstSignal
    getAnomalySignals.mockImplementation((ticker, { signal: abortSignal }) => {
      if (ticker === 'MSFT') firstSignal = abortSignal
      return Promise.resolve(page([signal]))
    })
    const { unmount } = render(<AnomaliesPage />)

    await screen.findByText(/Net income grew 12\.0%/)
    fireEvent.change(screen.getByRole('combobox', { name: 'Select company' }), { target: { value: 'AAPL' } })
    await waitFor(() => expect(getAnomalySignals).toHaveBeenCalledWith(
      'AAPL',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ))
    expect(firstSignal.aborted).toBe(true)

    unmount()
    expect(getAnomalySignals.mock.calls.at(-1)[1].signal.aborted).toBe(true)
  })
})
