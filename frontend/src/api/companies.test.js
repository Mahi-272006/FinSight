import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client.js', () => ({ apiClient: { get: vi.fn() } }))

import { apiClient } from './client.js'
import {
  getCompany, getCompanyFacts, getCompanyMetricHistory, getCompanyMetrics,
} from './companies.js'
import { InvalidApiResponseError } from './validation.js'

const company = {
  cik: 789019, ticker: 'MSFT', name: 'Microsoft Corporation', fiscal_year_end_month: 6,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
}
const metric = {
  id: 1, cik: 789019, metric: 'revenue_growth_yoy', value: 0.12, unit: 'ratio',
  fiscal_year: 2025, fiscal_period: 'FY', period_type: 'annual', formula: 'a / b - 1',
  inputs: { current: 10, prior: 9 }, source_accessions: ['accession-1'],
  calculated_at: '2026-01-01T00:00:00Z',
}
const fact = {
  id: 2, cik: 789019, metric: 'revenue', value: 100, unit: 'USD', period_type: 'annual',
  fiscal_year: 2025, fiscal_period: 'FY', period_start: null, period_end: null,
  source_concept: 'RevenueFromContractWithCustomerExcludingAssessedTax', form: '10-K',
  accession_number: '0000789019-25-000001', filed_at: null, source_url: null,
  is_restated: false, value_original: null, created_at: '2026-01-01T00:00:00Z',
}
const page = (items) => ({ items, total: items.length, limit: 20, offset: 0 })

describe('Companies API', () => {
  beforeEach(() => apiClient.get.mockReset())

  it('requests and validates company profile, calculated metrics, history, and reported facts', async () => {
    apiClient.get
      .mockResolvedValueOnce({ data: company })
      .mockResolvedValueOnce({ data: page([metric]) })
      .mockResolvedValueOnce({ data: page([metric]) })
      .mockResolvedValueOnce({ data: page([fact]) })
    const signal = new AbortController().signal

    expect(await getCompany('MSFT', { signal })).toEqual(company)
    expect(await getCompanyMetrics('MSFT', { limit: 20, offset: 0, signal })).toEqual(page([metric]))
    expect(await getCompanyMetricHistory('MSFT', 'revenue_growth_yoy', { signal })).toEqual(page([metric]))
    expect(await getCompanyFacts('MSFT', { signal })).toEqual(page([fact]))
    expect(apiClient.get.mock.calls.map(([path]) => path)).toEqual([
      '/companies/MSFT', '/companies/MSFT/metrics',
      '/companies/MSFT/metrics/revenue_growth_yoy/history', '/companies/MSFT/facts',
    ])
    expect(apiClient.get.mock.calls.every(([, config]) => config.signal === signal)).toBe(true)
  })

  it('accepts valid empty pages and rejects malformed financial records', async () => {
    apiClient.get.mockResolvedValueOnce({ data: page([]) })
    expect((await getCompanyMetrics('MSFT')).items).toEqual([])
    apiClient.get.mockResolvedValueOnce({ data: page([{ ...metric, inputs: null }]) })
    await expect(getCompanyMetrics('MSFT')).rejects.toBeInstanceOf(InvalidApiResponseError)
    apiClient.get.mockResolvedValueOnce({ data: { ...company, ticker: null } })
    await expect(getCompany('MSFT')).rejects.toBeInstanceOf(InvalidApiResponseError)
  })

  it('passes metric and period_type filters through as query parameters', async () => {
    apiClient.get
      .mockResolvedValueOnce({ data: page([metric]) })
      .mockResolvedValueOnce({ data: page([fact]) })

    await getCompanyMetrics('MSFT', { metric: 'revenue_growth_yoy' })
    expect(apiClient.get.mock.calls.at(-1)[1].params).toEqual({
      limit: 20, offset: 0, metric: 'revenue_growth_yoy',
    })

    await getCompanyFacts('MSFT', { periodType: 'annual' })
    expect(apiClient.get.mock.calls.at(-1)[1].params).toEqual({
      limit: 20, offset: 0, period_type: 'annual',
    })
  })
})
