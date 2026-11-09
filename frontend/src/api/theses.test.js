import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client.js', () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }))

import { apiClient } from './client.js'
import {
  assessThesis, createThesis, getAssessmentHistory, getThesis, getTheses,
} from './theses.js'
import { InvalidApiResponseError } from './validation.js'

const timestamp = '2026-01-01T00:00:00Z'

const evaluation = {
  condition_id: 3,
  description: 'revenue_growth_yoy >= 0.1 (FY2026)',
  metric: 'revenue_growth_yoy',
  comparator: '>=',
  threshold: 0.1,
  verdict: 'met',
  value: 0.1779,
  unit: 'ratio',
  tier: 'CALCULATED METRIC',
  fiscal_year: 2026,
  fiscal_period: 'FY',
  formula: '(current - prior) / abs(prior)',
  accessions: ['0000789019-26-000001'],
  reason: '0.1779 >= 0.1 -> holds',
}

const assessment = {
  id: 9,
  assessed_at: timestamp,
  status: 'Supported',
  status_changed_from: null,
  summary: [evaluation],
  explanation: 'Supported: 1/1 condition(s) met.',
  explanation_source: 'deterministic:condition-counts',
  signals: [],
}

const condition = {
  id: 3,
  description: 'revenue_growth_yoy >= 0.1 (FY2026)',
  metric: 'revenue_growth_yoy',
  comparator: '>=',
  threshold: 0.1,
  fiscal_year: 2026,
  fiscal_period: 'FY',
  creation_evidence: {
    metric: 'revenue_growth_yoy',
    comparator: '>=',
    threshold: 0.1,
    captured_at: timestamp,
    value: 0.1779,
    unit: 'ratio',
    tier: 'CALCULATED METRIC',
    period: 'FY2026',
    formula: '(current - prior) / abs(prior)',
    accessions: ['0000789019-26-000001'],
  },
}

const thesis = {
  id: 12,
  ticker: 'MSFT',
  statement: 'Microsoft sustains double-digit growth.',
  horizon: 'FY2026',
  status: 'Unassessed',
  created_at: timestamp,
  conditions: [condition],
  assessments: [],
}

const listItem = (overrides = {}) => ({
  id: 12,
  ticker: 'MSFT',
  statement: 'Microsoft sustains double-digit growth.',
  horizon: 'FY2026',
  status: 'Unassessed',
  created_at: timestamp,
  ...overrides,
})

const page = (items, total = items.length, limit = 50, offset = 0) => ({
  items, total, limit, offset,
})

describe('Theses API', () => {
  beforeEach(() => {
    apiClient.get.mockReset()
    apiClient.post.mockReset()
  })

  it('lists theses with pagination and passes the abort signal', async () => {
    apiClient.get.mockResolvedValue({ data: page([listItem()], 1) })
    const abort = new AbortController().signal

    expect(await getTheses({ limit: 20, offset: 0, signal: abort })).toEqual(page([listItem()], 1))
    expect(apiClient.get).toHaveBeenLastCalledWith('/theses', {
      params: { limit: 20, offset: 0 },
      signal: abort,
    })
  })

  it('fetches one thesis detail by id', async () => {
    apiClient.get.mockResolvedValue({ data: thesis })

    expect(await getThesis(12)).toEqual(thesis)
    expect(apiClient.get).toHaveBeenLastCalledWith('/theses/12', { signal: undefined })
  })

  it('posts creation payloads unchanged and validates the 201 body', async () => {
    apiClient.post.mockResolvedValue({ data: thesis })
    const payload = {
      ticker: 'MSFT',
      statement: 'Microsoft sustains double-digit growth.',
      horizon: 'FY2026',
      conditions: ['revenue_growth_yoy >= 0.10'],
      fiscal_year: 2026,
      fiscal_period: 'FY',
    }

    expect(await createThesis(payload)).toEqual(thesis)
    expect(apiClient.post).toHaveBeenLastCalledWith('/theses', payload, { signal: undefined })
  })

  it('assesses with interpret defaulting to false and passes true through', async () => {
    const result = {
      assessment_id: 9,
      thesis_id: 12,
      status: 'Supported',
      status_changed_from: null,
      evaluations: [evaluation],
      explanation: 'Supported: 1/1 condition(s) met.',
      explanation_source: 'deterministic:condition-counts',
      signals_context: [],
    }
    apiClient.post.mockResolvedValue({ data: result })

    expect(await assessThesis(12)).toEqual(result)
    expect(apiClient.post).toHaveBeenLastCalledWith(
      '/theses/12/assess',
      { interpret: false },
      { signal: undefined },
    )

    await assessThesis(12, { interpret: true })
    expect(apiClient.post).toHaveBeenLastCalledWith(
      '/theses/12/assess',
      { interpret: true },
      { signal: undefined },
    )
  })

  it('reads the append-only assessment history page', async () => {
    apiClient.get.mockResolvedValue({ data: page([assessment], 1) })

    expect(await getAssessmentHistory(12, { limit: 20, offset: 1 })).toEqual(page([assessment], 1))
    expect(apiClient.get).toHaveBeenLastCalledWith('/theses/12/assessments', {
      params: { limit: 20, offset: 1 },
      signal: undefined,
    })
  })

  it('rejects malformed details, snapshots, evaluations, and history rows', async () => {
    apiClient.get.mockResolvedValue({ data: { ...thesis, conditions: 'not-a-list' } })
    await expect(getThesis(12)).rejects.toBeInstanceOf(InvalidApiResponseError)

    apiClient.get.mockResolvedValue({
      data: { ...thesis, conditions: [{ ...condition, creation_evidence: { metric: 'x' } }] },
    })
    await expect(getThesis(12)).rejects.toBeInstanceOf(InvalidApiResponseError)

    apiClient.post.mockResolvedValue({
      data: {
        assessment_id: 9,
        thesis_id: 12,
        status: 'Supported',
        status_changed_from: null,
        evaluations: [{ ...evaluation, value: 'not-a-number' }],
        explanation: 'x',
        explanation_source: 'deterministic:condition-counts',
        signals_context: [],
      },
    })
    await expect(assessThesis(12)).rejects.toBeInstanceOf(InvalidApiResponseError)

    apiClient.get.mockResolvedValue({
      data: page([{ ...assessment, explanation_source: null }]),
    })
    await expect(getAssessmentHistory(12)).rejects.toBeInstanceOf(InvalidApiResponseError)
  })
})
