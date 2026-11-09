import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client.js', () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }))

import { apiClient } from './client.js'
import { getAnomalySignals, recomputeAnomalySignals } from './anomalies.js'
import { InvalidApiResponseError } from './validation.js'

const timestamp = '2026-01-01T00:00:00Z'
const signal = {
  id: 5,
  cik: 789019,
  ticker: 'MSFT',
  signal_type: 'growth_deceleration',
  severity: 'high',
  fiscal_year: 2025,
  fiscal_period: 'FY',
  period_type: 'annual',
  headline: 'Growth slowed across reported periods',
  explanation: 'A prompt to review the trend -- not a conclusion.',
  evidence: { deceleration: 0.12, threshold_high: 0.1, formula: 'prior_growth - current_growth' },
  source_accessions: ['0000789019-25-000001'],
  detected_at: timestamp,
}
const page = (items, total = items.length) => ({ items, total, limit: 20, offset: 0 })
const recomputeResult = (items = [signal]) => ({
  cik: 789019,
  ticker: 'MSFT',
  total: items.length,
  items,
})

describe('Anomalies API', () => {
  beforeEach(() => {
    apiClient.get.mockReset()
    apiClient.post.mockReset()
  })

  it('requests persisted signals with pagination and passes the signal_type filter', async () => {
    apiClient.get.mockResolvedValue({ data: page([signal]) })
    const abort = new AbortController().signal

    expect(await getAnomalySignals('MSFT', { limit: 20, offset: 0, signal: abort })).toEqual(page([signal]))
    expect(apiClient.get).toHaveBeenLastCalledWith('/anomalies/MSFT', {
      params: { limit: 20, offset: 0 },
      signal: abort,
    })

    await getAnomalySignals('MSFT', { signalType: 'growth_deceleration' })
    expect(apiClient.get).toHaveBeenLastCalledWith('/anomalies/MSFT', {
      params: { limit: 20, offset: 0, signal_type: 'growth_deceleration' },
      signal: undefined,
    })
  })

  it('accepts a legitimate empty page', async () => {
    apiClient.get.mockResolvedValue({ data: page([]) })
    expect((await getAnomalySignals('MSFT')).items).toEqual([])
  })

  it('rejects malformed signal records and invalid page envelopes', async () => {
    apiClient.get.mockResolvedValue({ data: page([{ ...signal, explanation: null }]) })
    await expect(getAnomalySignals('MSFT')).rejects.toBeInstanceOf(InvalidApiResponseError)

    apiClient.get.mockResolvedValue({ data: page([signal], -1) })
    await expect(getAnomalySignals('MSFT')).rejects.toBeInstanceOf(InvalidApiResponseError)
  })

  it('posts exactly the ticker and validates the complete recomputation response', async () => {
    apiClient.post.mockResolvedValue({ data: recomputeResult() })
    const abort = new AbortController().signal

    expect(await recomputeAnomalySignals('MSFT', { signal: abort })).toEqual(recomputeResult())
    expect(apiClient.post).toHaveBeenCalledWith(
      '/anomalies/recompute',
      { ticker: 'MSFT' },
      { signal: abort },
    )
  })

  it('accepts a successful recomputation with no signals', async () => {
    apiClient.post.mockResolvedValue({ data: recomputeResult([]) })

    await expect(recomputeAnomalySignals('AAPL')).resolves.toEqual(recomputeResult([]))
    expect(apiClient.post).toHaveBeenCalledWith(
      '/anomalies/recompute',
      { ticker: 'AAPL' },
      { signal: undefined },
    )
  })

  it('rejects malformed recomputation envelopes and nested signal records', async () => {
    const invalidResults = [
      { ...recomputeResult(), cik: '789019' },
      { ...recomputeResult(), total: -1 },
      { ...recomputeResult(), total: 2 },
      { ...recomputeResult(), items: [{ ...signal, evidence: null }] },
      { ...recomputeResult(), items: [{ ...signal, source_accessions: [''] }] },
    ]

    for (const data of invalidResults) {
      apiClient.post.mockResolvedValueOnce({ data })
      await expect(recomputeAnomalySignals('MSFT')).rejects.toBeInstanceOf(InvalidApiResponseError)
    }
  })
})
