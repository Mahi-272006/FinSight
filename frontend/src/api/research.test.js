import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client.js', () => ({
  apiClient: { post: vi.fn() },
}))

import { apiClient } from './client.js'
import { InvalidApiResponseError } from './validation.js'
import { RESEARCH_TIMEOUT_MS, runResearch } from './research.js'

const validResearchResult = {
  question: 'What supports the margin profile?',
  ticker: 'MSFT',
  company: { cik: 789019, ticker: 'MSFT', name: 'Microsoft Corporation' },
  final_answer: 'Revenue of $245,122,000,000 [VERIFIED FACT ...0000789019-26-000001].',
  citations: ['0000789019-26-000001'],
  limitations: ['removed sentence [unverified number(s): 123]'],
  trace: ['resolve: Microsoft Corporation (CIK 789019)', 'finalize: 1 citation(s)'],
  validation: {
    ok: true,
    citations: ['0000789019-26-000001'],
    unsupported_citations: [],
    verified_numbers: ['245,122,000,000'],
    unverified_numbers: [],
    notes: [],
  },
  iterations: 2,
  revises: 1,
  tool_calls: [
    { tool: 'search_filing_sections', args: { query: 'margin', k: 4 }, chunk_ids: ['0000789019-26-000001:12'] },
  ],
  thread_id: '4e4e0d6a-6e6c-4d5f-9d2f-3a1b2c3d4e5f',
  save_requested: true,
  report_id: 7,
}

describe('runResearch', () => {
  beforeEach(() => {
    apiClient.post.mockReset()
  })

  it('posts exactly { ticker, question, save } with the signal and a 300000ms timeout', async () => {
    const signal = new AbortController().signal
    apiClient.post.mockResolvedValue({ data: validResearchResult })

    const result = await runResearch({
      ticker: 'MSFT',
      question: 'What supports the margin profile?',
      save: true,
      signal,
    })

    expect(result).toEqual(validResearchResult)
    expect(apiClient.post).toHaveBeenCalledTimes(1)
    expect(apiClient.post).toHaveBeenCalledWith(
      '/research',
      { ticker: 'MSFT', question: 'What supports the margin profile?', save: true },
      { signal, timeout: RESEARCH_TIMEOUT_MS },
    )
    expect(RESEARCH_TIMEOUT_MS).toBe(300000)
  })

  it('defaults save to true when omitted', async () => {
    apiClient.post.mockResolvedValue({ data: validResearchResult })

    await runResearch({ ticker: 'MSFT', question: 'Question?' })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/research',
      { ticker: 'MSFT', question: 'Question?', save: true },
      { signal: undefined, timeout: RESEARCH_TIMEOUT_MS },
    )
  })

  it('passes save=false through unchanged', async () => {
    apiClient.post.mockResolvedValue({ data: validResearchResult })

    await runResearch({ ticker: 'AAPL', question: 'Question?', save: false })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/research',
      { ticker: 'AAPL', question: 'Question?', save: false },
      { signal: undefined, timeout: RESEARCH_TIMEOUT_MS },
    )
  })

  it('accepts a contract-valid response including a null validation and null report', async () => {
    const data = {
      ...validResearchResult,
      validation: null,
      report_id: null,
      tool_calls: [],
      save_requested: false,
    }
    apiClient.post.mockResolvedValue({ data })

    await expect(runResearch({ ticker: 'MSFT', question: 'Q?' })).resolves.toEqual(data)
  })

  it('rejects malformed company, validation, tool-call and report fields', async () => {
    const cases = [
      { ...validResearchResult, company: null },
      { ...validResearchResult, company: { cik: '789019', ticker: 'MSFT', name: 'Microsoft Corporation' } },
      { ...validResearchResult, validation: { citations: [] } },
      { ...validResearchResult, validation: { ok: 'yes', citations: [] } },
      { ...validResearchResult, tool_calls: [{ tool: 'search_filing_sections', args: {}, chunk_ids: [3] }] },
      { ...validResearchResult, tool_calls: [{ tool: 'search_filing_sections', args: [], chunk_ids: [] }] },
      { ...validResearchResult, report_id: '7' },
      { ...validResearchResult, save_requested: 'true' },
      { ...validResearchResult, iterations: -1 },
    ]

    for (const data of cases) {
      apiClient.post.mockResolvedValue({ data })
      await expect(
        runResearch({ ticker: 'MSFT', question: 'Q?' }),
      ).rejects.toBeInstanceOf(InvalidApiResponseError)
    }
  })

  it('rejects a response that is not a record', async () => {
    apiClient.post.mockResolvedValue({ data: ['not', 'a', 'record'] })

    await expect(runResearch({ ticker: 'MSFT', question: 'Q?' })).rejects.toBeInstanceOf(
      InvalidApiResponseError,
    )
  })
})
