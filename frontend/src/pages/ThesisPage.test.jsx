import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/theses.js', () => ({
  getTheses: vi.fn(),
  getThesis: vi.fn(),
  createThesis: vi.fn(),
  assessThesis: vi.fn(),
  getAssessmentHistory: vi.fn(),
}))
vi.mock('../api/overview.js', () => ({ getCompanies: vi.fn() }))
vi.mock('../api/companies.js', () => ({ getCompanyMetrics: vi.fn() }))

import {
  assessThesis, createThesis, getAssessmentHistory, getThesis, getTheses,
} from '../api/theses.js'
import { getCompanies } from '../api/overview.js'
import { getCompanyMetrics } from '../api/companies.js'
import ThesisPage from './ThesisPage.jsx'

const timestamp = '2026-01-01T00:00:00Z'
const companies = [
  { cik: 789019, ticker: 'MSFT', name: 'Microsoft Corporation', fiscal_year_end_month: 6, created_at: timestamp, updated_at: timestamp },
  { cik: 320193, ticker: 'AAPL', name: 'Apple Inc.', fiscal_year_end_month: 9, created_at: timestamp, updated_at: timestamp },
]

const creationEvidence = {
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
}

const condition = {
  id: 3,
  description: 'revenue_growth_yoy >= 0.1 (FY2026)',
  metric: 'revenue_growth_yoy',
  comparator: '>=',
  threshold: 0.1,
  fiscal_year: 2026,
  fiscal_period: 'FY',
  creation_evidence: creationEvidence,
}

const evaluationMet = {
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

const evaluationNotMet = {
  ...evaluationMet,
  verdict: 'not_met',
  value: 0.05,
  reason: '0.05 >= 0.1 -> does not hold',
}

const signalContext = {
  signal_type: 'earnings_cashflow_divergence',
  severity: 'high',
  period: 'FY2026',
  headline: 'Net income outpaces operating cash flow',
  explanation: 'review the cash-flow statement',
}

const assessmentOne = {
  id: 9,
  assessed_at: timestamp,
  status: 'Supported',
  status_changed_from: null,
  summary: [evaluationMet],
  explanation: 'Supported: 1/1 condition(s) met.',
  explanation_source: 'deterministic:condition-counts',
  signals: [],
}

const assessmentTwo = {
  id: 14,
  assessed_at: '2026-02-03T09:30:00Z',
  status: 'Contradicted',
  status_changed_from: 'Supported',
  summary: [evaluationNotMet],
  explanation: 'Status changed from Supported to Contradicted. Contradicted: 0/1 condition(s) met.',
  explanation_source: 'llm:groq/llama-3.1-8b',
  signals: [signalContext],
}

const thesisDetail = {
  id: 12,
  ticker: 'MSFT',
  statement: 'Microsoft sustains double-digit growth through FY2026.',
  horizon: 'FY2026',
  status: 'Contradicted',
  created_at: timestamp,
  conditions: [condition],
  assessments: [assessmentOne, assessmentTwo],
}

const secondThesis = {
  ...thesisDetail,
  id: 55,
  ticker: 'AAPL',
  statement: 'Apple keeps services margins above 30%.',
  status: 'Unassessed',
  assessments: [],
}

const listItem = (overrides = {}) => ({
  id: 12,
  ticker: 'MSFT',
  statement: 'Microsoft sustains double-digit growth through FY2026.',
  horizon: 'FY2026',
  status: 'Contradicted',
  created_at: timestamp,
  ...overrides,
})

const page = (items, total = items.length, limit = 50, offset = 0) => ({
  items, total, limit, offset,
})

const newThesis = {
  id: 77,
  ticker: 'MSFT',
  statement: 'Microsoft sustains growth.',
  horizon: 'FY2026',
  status: 'Unassessed',
  created_at: timestamp,
  conditions: [condition],
  assessments: [],
}

const assessResult = {
  assessment_id: 21,
  thesis_id: 12,
  status: 'Contradicted',
  status_changed_from: null,
  evaluations: [evaluationNotMet],
  explanation: 'Contradicted: 0/1 condition(s) met.',
  explanation_source: 'deterministic:condition-counts',
  signals_context: [],
}

function setupSuccess({
  list = page([listItem()]),
  detail = thesisDetail,
  history = page([assessmentOne, assessmentTwo], 2, 20, 0),
  metrics = page([], 0, 100, 0),
} = {}) {
  getCompanies.mockResolvedValue(page(companies, 2, 100, 0))
  getTheses.mockResolvedValue(list)
  getThesis.mockResolvedValue(detail)
  getAssessmentHistory.mockResolvedValue(history)
  getCompanyMetrics.mockResolvedValue(metrics)
  createThesis.mockResolvedValue(newThesis)
  assessThesis.mockResolvedValue(assessResult)
}

const panelWithTitle = (title) => screen.getByText(title).closest('.panel')

describe('Thesis page', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('renders the register and detail with conditions, snapshots, and history', async () => {
    setupSuccess()
    render(<ThesisPage />)

    expect(await screen.findByText('FY2026 · created Jan 1, 2026')).toBeTruthy()
    expect(await screen.findByText('MSFT · FY2026')).toBeTruthy()

    const register = panelWithTitle('All theses')
    expect(within(register).getByText('Microsoft sustains double-digit growth through FY2026.')).toBeTruthy()
    expect(within(register).getByText('Contradicted')).toBeTruthy()

    const detail = panelWithTitle('MSFT · FY2026')
    expect(within(detail).getByText('Microsoft sustains double-digit growth through FY2026.')).toBeTruthy()
    // Latest assessment carries the deterministic status plus LLM provenance.
    expect(within(detail).getByText('The status badge is the authoritative deterministic result, derived from condition verdicts alone.')).toBeTruthy()
    expect(within(detail).getAllByText('LLM interpretation · groq/llama-3.1-8b').length).toBeGreaterThanOrEqual(1)
    expect(within(detail).getByText('Transition recorded: Supported → Contradicted')).toBeTruthy()

    // Conditions with creation-time evidence snapshots.
    const conditions = panelWithTitle('Measurable commitments')
    expect(within(conditions).getByText('Evidence at creation')).toBeTruthy()
    expect(within(conditions).getByText('CALCULATED METRIC')).toBeTruthy()
    expect(within(conditions).getByText('(current - prior) / abs(prior)')).toBeTruthy()
    expect(within(conditions).getByText('0000789019-26-000001')).toBeTruthy()
    expect(within(conditions).getByText('Period: FY2026')).toBeTruthy()

    // History timeline: transitions, verdicts, provenance, context-only signals.
    const history = panelWithTitle('Assessment timeline')
    expect(within(history).getByText('Supported')).toBeTruthy()
    expect(within(history).getByText('Supported → Contradicted')).toBeTruthy()
    expect(within(history).getByText('Deterministic summary')).toBeTruthy()
    expect(within(history).getAllByText('LLM interpretation · groq/llama-3.1-8b').length).toBeGreaterThanOrEqual(1)
    expect(within(history).getByText('Met')).toBeTruthy()
    expect(within(history).getByText('Not met')).toBeTruthy()
    expect(within(history).getByText(/0\.1779 ratio · CALCULATED METRIC · FY2026/)).toBeTruthy()
    expect(within(history).getAllByText('Formula: (current - prior) / abs(prior)').length).toBeGreaterThanOrEqual(1)
    expect(within(history).getAllByText(/Accessions: 0000789019-26-000001/).length).toBeGreaterThanOrEqual(1)
    expect(within(history).getByText('Context only: 1 investigation signal(s) attached — never verdicts.')).toBeTruthy()
    expect(within(history).getByText(/Feb 3, 2026/)).toBeTruthy()

    // Signals on the latest assessment are labelled context only.
    expect(screen.getByText('Investigation signals on the latest assessment')).toBeTruthy()
    expect(screen.getByText('Attached as context only. Signals never determine or change thesis status.')).toBeTruthy()
  })

  it('shows honest empty states when no theses exist', async () => {
    setupSuccess({ list: page([], 0) })
    render(<ThesisPage />)

    expect(await screen.findByText('No investment theses yet')).toBeTruthy()
    expect(screen.getByText('No thesis selected')).toBeTruthy()
    expect(getThesis).not.toHaveBeenCalled()
    expect(getAssessmentHistory).not.toHaveBeenCalled()
  })

  it('recovers from a register failure through retry', async () => {
    setupSuccess()
    getTheses.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'boom' } } })
    render(<ThesisPage />)

    const register = panelWithTitle('All theses')
    expect(await within(register).findByText('The backend encountered a problem. Please try again later.')).toBeTruthy()
    expect(screen.queryByText(/boom/)).toBeNull()

    fireEvent.click(within(register).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('FY2026 · created Jan 1, 2026')).toBeTruthy()
  })

  it('treats a missing thesis as an API error and recovers through retry', async () => {
    setupSuccess()
    getThesis
      .mockRejectedValueOnce({ response: { status: 404, data: { detail: 'thesis 12 not found' } } })
      .mockResolvedValueOnce(thesisDetail)
    render(<ThesisPage />)

    expect(await screen.findByText('The requested records are not available.')).toBeTruthy()
    expect(screen.queryByText(/thesis 12 not found/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('MSFT · FY2026')).toBeTruthy()
  })

  it('assesses deterministically by default and refreshes detail and history', async () => {
    setupSuccess()
    render(<ThesisPage />)
    await screen.findByText('MSFT · FY2026')

    fireEvent.click(screen.getByRole('button', { name: 'Assess now' }))

    await waitFor(() => expect(assessThesis).toHaveBeenCalledWith(12, { interpret: false }))
    await waitFor(() => expect(getThesis.mock.calls.length).toBe(2))
    await waitFor(() => expect(getAssessmentHistory.mock.calls.length).toBe(2))
  })

  it('passes the LLM interpretation opt-in through to the assess call', async () => {
    setupSuccess()
    render(<ThesisPage />)
    await screen.findByText('MSFT · FY2026')

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Assess now' }))

    await waitFor(() => expect(assessThesis).toHaveBeenCalledWith(12, { interpret: true }))
  })

  it('shows a safe message when the assessment fails', async () => {
    setupSuccess()
    assessThesis.mockRejectedValue({ response: { status: 500, data: { detail: 'database DSN secret' } } })
    render(<ThesisPage />)
    await screen.findByText('MSFT · FY2026')

    fireEvent.click(screen.getByRole('button', { name: 'Assess now' }))

    const alerts = await screen.findAllByRole('alert')
    expect(alerts.map((node) => node.textContent).join(' '))
      .toBe('The backend encountered a problem. Please try again later.')
    expect(screen.queryByText(/DSN secret/)).toBeNull()
    expect(getThesis.mock.calls.length).toBe(1) // failed run refreshes nothing
  })

  it('creates a thesis with the exact payload, refreshes the register, and never auto-assesses', async () => {
    setupSuccess({
      metrics: page([
        {
          id: 1,
          cik: 789019,
          metric: 'revenue_growth_yoy',
          value: 0.1779,
          unit: 'ratio',
          fiscal_year: 2026,
          fiscal_period: 'FY',
          period_type: 'annual',
          formula: '(current - prior) / abs(prior)',
          inputs: {},
          source_accessions: ['0000789019-26-000001'],
          calculated_at: timestamp,
        },
      ], 1, 100, 0),
    })
    render(<ThesisPage />)
    await screen.findByText('MSFT · FY2026')

    fireEvent.click(screen.getByRole('button', { name: 'New thesis' }))
    fireEvent.change(await screen.findByRole('combobox', { name: 'Company' }), { target: { value: 'MSFT' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Statement' }), {
      target: { value: 'Microsoft sustains growth.' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: 'Horizon' }), { target: { value: 'FY2026' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Condition 1' }), {
      target: { value: 'revenue_growth_yoy >= 0.10' },
    })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Fiscal year (optional)' }), {
      target: { value: '2026' },
    })
    fireEvent.change(screen.getByRole('combobox', { name: 'Fiscal period (optional)' }), {
      target: { value: 'FY' },
    })

    // Metric suggestions come from the company metrics endpoint.
    await waitFor(() => {
      expect(getCompanyMetrics).toHaveBeenCalledWith('MSFT', expect.objectContaining({ limit: 100 }))
      expect(document.querySelector('#thesis-metric-suggestions option[value="revenue_growth_yoy"]'))
        .not.toBeNull()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Create thesis' }))

    await waitFor(() => expect(createThesis).toHaveBeenCalledWith({
      ticker: 'MSFT',
      statement: 'Microsoft sustains growth.',
      horizon: 'FY2026',
      conditions: ['revenue_growth_yoy >= 0.10'],
      fiscal_year: 2026,
      fiscal_period: 'FY',
    }))
    expect(screen.queryByText('Create an investment thesis')).toBeNull()
    await waitFor(() => expect(getTheses.mock.calls.length).toBe(2))
    await waitFor(() => expect(getThesis).toHaveBeenCalledWith(
      77,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ))
    expect(assessThesis).not.toHaveBeenCalled()
  })

  it('blocks submission on client-side syntax validation without calling the backend', async () => {
    setupSuccess()
    render(<ThesisPage />)
    await screen.findByText('MSFT · FY2026')

    fireEvent.click(screen.getByRole('button', { name: 'New thesis' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Create thesis' }))

    expect(await screen.findByText('Choose a company.')).toBeTruthy()
    expect(screen.getByText('A statement is required.')).toBeTruthy()
    expect(screen.getByText('A horizon is required.')).toBeTruthy()
    expect(screen.getByText('Add at least one condition.')).toBeTruthy()
    expect(createThesis).not.toHaveBeenCalled()

    fireEvent.change(screen.getByRole('combobox', { name: 'Company' }), { target: { value: 'MSFT' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Statement' }), { target: { value: 'A statement.' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Horizon' }), { target: { value: 'FY2026' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Condition 1' }), { target: { value: 'not a condition' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create thesis' }))

    expect(await screen.findByText(
      "Each condition must read 'metric OP threshold', e.g. revenue_growth_yoy >= 0.10.",
    )).toBeTruthy()
    expect(createThesis).not.toHaveBeenCalled()

    // Live per-row feedback while typing.
    expect(screen.getByRole('combobox', { name: 'Condition 1' }).getAttribute('aria-invalid')).toBe('true')
  })

  it('shows backend validation failures safely and keeps the form open', async () => {
    setupSuccess()
    createThesis.mockRejectedValue({
      response: { status: 400, data: { detail: 'Invalid thesis input.' } },
    })
    render(<ThesisPage />)
    await screen.findByText('MSFT · FY2026')

    fireEvent.click(screen.getByRole('button', { name: 'New thesis' }))
    fireEvent.change(await screen.findByRole('combobox', { name: 'Company' }), { target: { value: 'MSFT' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Statement' }), { target: { value: 'A statement.' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Horizon' }), { target: { value: 'FY2026' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Condition 1' }), {
      target: { value: 'revenue_growth_yoy >= 0.10' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create thesis' }))

    expect(await screen.findByText(
      'The request could not be processed. Check the information and try again.',
    )).toBeTruthy()
    expect(screen.getByText('Create an investment thesis')).toBeTruthy()
  })

  it('paginates the assessment history through the history endpoint', async () => {
    setupSuccess({ history: page([assessmentOne], 45, 20, 0) })
    render(<ThesisPage />)
    await screen.findByText('MSFT · FY2026')

    const history = panelWithTitle('Assessment timeline')
    fireEvent.click(within(history).getByRole('button', { name: 'Next page' }))

    await waitFor(() => expect(getAssessmentHistory).toHaveBeenCalledWith(
      12,
      expect.objectContaining({ offset: 20 }),
    ))
  })

  it('cancels the previous detail request when another thesis is selected', async () => {
    setupSuccess({
      list: page([
        listItem(),
        listItem({ id: 55, ticker: 'AAPL', statement: 'Apple keeps services margins above 30%.', status: 'Unassessed' }),
      ], 2),
    })
    const detailSignals = []
    getThesis.mockImplementation((thesisId, { signal } = {}) => {
      detailSignals.push(signal)
      return Promise.resolve(thesisId === 55 ? secondThesis : thesisDetail)
    })
    render(<ThesisPage />)

    await screen.findByText('MSFT · FY2026')
    fireEvent.click(screen.getByRole('button', { name: /Apple keeps services margins/ }))

    await waitFor(() => expect(getThesis).toHaveBeenCalledWith(
      55,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ))
    expect(detailSignals[0].aborted).toBe(true)
    expect(await screen.findByText('AAPL · FY2026')).toBeTruthy()
  })
})
