import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client.js', () => ({
  apiClient: { get: vi.fn() },
}))

import { apiClient } from './client.js'
import { InvalidApiResponseError } from './validation.js'
import { getReport } from './reports.js'

// The stored body is asdict(ResearchResult): exactly these 11 fields. It
// never contains thread_id, save_requested or report_id — those live at the
// detail response's top level (thread id) or only on the /research response.
const storedBody = {
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
}

const validReportDetail = {
  id: 7,
  cik: 789019,
  ticker: 'MSFT',
  question: 'What supports the margin profile?',
  thread_id: '4e4e0d6a-6e6c-4d5f-9d2f-3a1b2c3d4e5f',
  created_at: '2026-11-09T23:15:00.123456+00:00',
  result: storedBody,
}

describe('getReport', () => {
  beforeEach(() => {
    apiClient.get.mockReset()
  })

  it('gets /reports/{id} with the abort signal and returns the validated detail', async () => {
    const signal = new AbortController().signal
    apiClient.get.mockResolvedValue({ data: validReportDetail })

    const detail = await getReport(7, { signal })

    expect(detail).toEqual(validReportDetail)
    expect(apiClient.get).toHaveBeenCalledTimes(1)
    expect(apiClient.get).toHaveBeenCalledWith('/reports/7', { signal })
  })

  it('accepts the stored 11-field body that carries no response-only metadata', async () => {
    expect(storedBody).not.toHaveProperty('thread_id')
    expect(storedBody).not.toHaveProperty('save_requested')
    expect(storedBody).not.toHaveProperty('report_id')
    apiClient.get.mockResolvedValue({ data: validReportDetail })

    const detail = await getReport(7)

    expect(detail.result).toEqual(storedBody)
  })

  it('accepts a null validation report and empty tool calls', async () => {
    const data = {
      ...validReportDetail,
      result: { ...storedBody, validation: null, tool_calls: [], limitations: [] },
    }
    apiClient.get.mockResolvedValue({ data })

    await expect(getReport(7)).resolves.toEqual(data)
  })

  it('rejects malformed summary and stored-result fields', async () => {
    const cases = [
      { ...validReportDetail, id: '7' },
      { ...validReportDetail, cik: 0 },
      { ...validReportDetail, created_at: 'not-a-date' },
      { ...validReportDetail, thread_id: '' },
      { ...validReportDetail, result: undefined },
      { ...validReportDetail, result: null },
      { ...validReportDetail, result: { ...storedBody, company: null } },
      { ...validReportDetail, result: { ...storedBody, validation: { citations: [] } } },
      { ...validReportDetail, result: { ...storedBody, iterations: -1 } },
      { ...validReportDetail, result: { ...storedBody, citations: '0000789019-26-000001' } },
      { ...validReportDetail, result: { ...storedBody, tool_calls: [{ tool: 'x', args: [], chunk_ids: [3] }] } },
      { ...validReportDetail, result: { ...storedBody, question: '' } },
    ]

    for (const data of cases) {
      apiClient.get.mockResolvedValue({ data })
      await expect(getReport(7)).rejects.toBeInstanceOf(InvalidApiResponseError)
    }
  })

  it('rejects a response that is not a record', async () => {
    apiClient.get.mockResolvedValue({ data: ['not', 'a', 'record'] })

    await expect(getReport(7)).rejects.toBeInstanceOf(InvalidApiResponseError)
  })
})
