import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/overview.js', () => ({ getCompanies: vi.fn() }))
vi.mock('../api/companies.js', () => ({
  getCompany: vi.fn(),
  getCompanyFacts: vi.fn(),
  getCompanyMetricHistory: vi.fn(),
  getCompanyMetrics: vi.fn(),
}))

import { getCompanies } from '../api/overview.js'
import { getCompany, getCompanyFacts, getCompanyMetricHistory, getCompanyMetrics } from '../api/companies.js'
import CompaniesPage from './CompaniesPage.jsx'

const timestamp = '2026-01-01T00:00:00Z'
const companies = [
  { cik: 789019, ticker: 'MSFT', name: 'Microsoft Corporation', fiscal_year_end_month: 6, created_at: timestamp, updated_at: timestamp },
  { cik: 320193, ticker: 'AAPL', name: 'Apple Inc.', fiscal_year_end_month: 9, created_at: timestamp, updated_at: timestamp },
]
const company = (entry) => ({ ...entry })
const metric = (id, fiscalYear, value) => ({
  id, cik: 789019, metric: 'revenue_growth_yoy', value, unit: 'ratio',
  fiscal_year: fiscalYear, fiscal_period: 'FY', period_type: 'annual', formula: 'current / prior - 1',
  inputs: { current: value, prior: value - 1 }, source_accessions: [`accession-${id}`], calculated_at: timestamp,
})
const fact = {
  id: 11, cik: 789019, metric: 'revenue', value: 1200000, unit: 'USD', period_type: 'annual',
  fiscal_year: 2025, fiscal_period: 'FY', period_start: null, period_end: timestamp,
  source_concept: 'RevenueFromContractWithCustomerExcludingAssessedTax', form: '10-K',
  accession_number: '0000789019-25-000001', filed_at: timestamp,
  source_url: 'https://www.sec.gov/Archives/edgar/data/789019/example',
  is_restated: false, value_original: null, created_at: timestamp,
}
const page = (items, total = items.length) => ({ items, total, limit: 20, offset: 0 })

function setupSuccess({ metricItems = [metric(1, 2024, 0.1), metric(2, 2025, 0.12)], factItems = [fact] } = {}) {
  getCompanies.mockResolvedValue(page(companies))
  getCompany.mockImplementation(async (ticker) => company(companies.find((item) => item.ticker === ticker)))
  getCompanyMetrics.mockResolvedValue(page(metricItems))
  getCompanyFacts.mockResolvedValue(page(factItems))
  getCompanyMetricHistory.mockResolvedValue(page(metricItems))
}

describe('Companies page', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('shows loading and then company identity, metrics, history, and reported filing evidence', async () => {
    setupSuccess()
    render(<CompaniesPage />)
    expect(screen.getByText('Loading supported companies')).toBeTruthy()
    expect(await screen.findByText('Microsoft Corporation')).toBeTruthy()
    expect(screen.getByText('Financial facts from filings')).toBeTruthy()
    expect(screen.getAllByText('accession-1').length).toBeGreaterThan(0)
    expect(screen.getByText('0000789019-25-000001')).toBeTruthy()
    expect(screen.getByRole('link', { name: /SEC filing/ }).getAttribute('href')).toContain('sec.gov')
    expect(await screen.findByRole('img', { name: /revenue growth yoy historical values/ })).toBeTruthy()
  })

  it('shows honest empty states when APIs return legitimate empty pages or no metric history', async () => {
    setupSuccess({ metricItems: [], factItems: [] })
    getCompanyMetricHistory.mockRejectedValue({ response: { status: 404 } })
    render(<CompaniesPage />)
    expect(await screen.findByText('No calculated metrics available')).toBeTruthy()
    expect(screen.getByText('No reported facts available')).toBeTruthy()
    expect(screen.getByText('No history for this metric')).toBeTruthy()
  })

  it('shows safe errors when the companies endpoint fails', async () => {
    getCompanies.mockRejectedValue({ response: { status: 500, data: { detail: 'database DSN secret' } } })
    render(<CompaniesPage />)
    expect(await screen.findByText('The backend encountered a problem. Please try again later.')).toBeTruthy()
    expect(screen.queryByText('database DSN secret')).toBeNull()
  })

  it('switches selected company and cancels the prior company request on unmount', async () => {
    setupSuccess()
    let firstSignal
    getCompany.mockImplementation((ticker, { signal }) => {
      if (ticker === 'MSFT') firstSignal = signal
      return Promise.resolve(company(companies.find((item) => item.ticker === ticker)))
    })
    const { unmount } = render(<CompaniesPage />)
    await screen.findByText('Microsoft Corporation')
    fireEvent.change(screen.getByRole('combobox', { name: 'Select company' }), { target: { value: 'AAPL' } })
    await waitFor(() => expect(getCompany).toHaveBeenCalledWith('AAPL', expect.objectContaining({ signal: expect.any(AbortSignal) })))
    expect(await screen.findByText('Apple Inc.')).toBeTruthy()
    unmount()
    expect(firstSignal.aborted).toBe(true)
  })

  it('filters the metrics table by the selected metric', async () => {
    setupSuccess()
    render(<CompaniesPage />)
    await screen.findByText('Microsoft Corporation')
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by metric' }), { target: { value: 'net_income_margin' } })
    await waitFor(() => expect(getCompanyMetrics).toHaveBeenLastCalledWith(
      'MSFT',
      expect.objectContaining({ metric: 'net_income_margin' }),
    ))
  })

  it('shows an honest empty state when a metric filter has no stored observations', async () => {
    setupSuccess()
    getCompanyMetrics.mockRejectedValue({ response: { status: 404 } })
    render(<CompaniesPage />)
    expect(await screen.findByText('No observations for this metric')).toBeTruthy()
  })

  it('discloses when history exceeds the API page limit', async () => {
    setupSuccess()
    getCompanyMetricHistory.mockResolvedValue({
      items: [metric(1, 2024, 0.1), metric(2, 2025, 0.12)],
      total: 92,
      limit: 100,
      offset: 0,
    })
    render(<CompaniesPage />)
    expect(await screen.findByText(/earliest 2 of 92 stored observations/)).toBeTruthy()
  })
})
