import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client.js', () => ({
  apiClient: { get: vi.fn() },
}))

import { apiClient } from './client.js'
import {
  getCompanies,
  getCompanyAnomalies,
  getReports,
  getTheses,
  getWatchlist,
  InvalidApiResponseError,
} from './overview.js'
import { validatePageResponse } from './validation.js'

const timestamp = '2026-10-31T12:00:00Z'
const fixtures = {
  companies: {
    cik: 789019,
    ticker: 'MSFT',
    name: 'Microsoft Corporation',
    fiscal_year_end_month: 6,
    created_at: timestamp,
    updated_at: timestamp,
  },
  watchlist: { cik: 789019, ticker: 'MSFT', created_at: timestamp },
  reports: {
    id: 1,
    cik: 789019,
    ticker: 'MSFT',
    question: 'Review the reported evidence.',
    thread_id: 'thread-1',
    created_at: timestamp,
  },
  theses: {
    id: 2,
    ticker: 'MSFT',
    statement: 'Revenue growth remains durable.',
    horizon: 'Long term',
    status: 'active',
    created_at: timestamp,
  },
  anomalies: {
    id: 3,
    cik: 789019,
    ticker: 'MSFT',
    signal_type: 'growth_deceleration',
    severity: 'elevated',
    fiscal_year: 2025,
    fiscal_period: 'FY',
    period_type: 'annual',
    headline: 'Growth decelerated.',
    explanation: 'Reported growth slowed.',
    evidence: { observed_values: [1, 2] },
    source_accessions: ['0000789019-25-000001'],
    detected_at: timestamp,
  },
}

function page(items = [], { total = items.length, limit = 20, offset = 0 } = {}) {
  return { items, total, limit, offset }
}

describe('paginated API response validation', () => {
  it.each(Object.entries(fixtures))('accepts a valid %s page', (type, item) => {
    expect(validatePageResponse(page([item]), type).items).toEqual([item])
  })

  it('accepts a legitimate empty page', () => {
    expect(validatePageResponse(page([], { total: 0 }), 'companies')).toEqual(page([], { total: 0 }))
    expect(validatePageResponse(page([], { total: 3, limit: 20, offset: 12 }), 'companies'))
      .toEqual(page([], { total: 3, limit: 20, offset: 12 }))
  })

  it.each([
    [{ total: 0, limit: 20, offset: 0 }, 'missing items'],
    [{ items: [], limit: 20, offset: 0 }, 'missing total'],
    [{ items: null, total: 0, limit: 20, offset: 0 }, 'non-array items'],
    [{ items: [], total: -1, limit: 20, offset: 0 }, 'negative total'],
    [{ items: [], total: 1.5, limit: 20, offset: 0 }, 'non-integer total'],
    [{ items: [{}], total: 1, limit: 20, offset: 0 }, 'malformed item'],
    [{ items: [fixtures.companies], total: 1, limit: 1, offset: 1 }, 'inconsistent count'],
  ])('rejects malformed page envelopes (%s)', (response) => {
    expect(() => validatePageResponse(response, 'companies')).toThrow(InvalidApiResponseError)
  })

  it('rejects an item with missing fields for its endpoint', () => {
    expect(() => validatePageResponse(page([{ ...fixtures.reports, question: null }]), 'reports'))
      .toThrow(InvalidApiResponseError)
  })
})

describe('Overview API request configuration', () => {
  beforeEach(() => apiClient.get.mockResolvedValue({ data: page([]) }))

  it('passes cancellation signals to every Overview request helper', async () => {
    const signal = new AbortController().signal
    await getCompanies({ limit: 20, signal })
    await getWatchlist({ limit: 8, signal })
    await getReports({ limit: 5, signal })
    await getTheses({ limit: 5, signal })
    await getCompanyAnomalies('MSFT', { limit: 5, signal })

    expect(apiClient.get.mock.calls.map(([, config]) => config.signal))
      .toEqual([signal, signal, signal, signal, signal])
  })
})
