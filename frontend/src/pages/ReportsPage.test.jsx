import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/overview.js', () => ({ getReports: vi.fn() }))
vi.mock('../api/reports.js', () => ({ getReport: vi.fn() }))

import { getReports } from '../api/overview.js'
import { getReport } from '../api/reports.js'
import ReportsPage from './ReportsPage.jsx'

// Late-evening UTC: a local-time date implementation would render a
// different calendar day on most machines, so this pins the UTC formatting.
const timestamp = '2026-01-15T23:30:00Z'

const summary12 = {
  id: 12,
  cik: 789019,
  ticker: 'MSFT',
  question: 'How durable is the reported growth?',
  thread_id: 'thread-12',
  created_at: timestamp,
}
const summary11 = {
  id: 11,
  cik: 320193,
  ticker: 'AAPL',
  question: 'Does Apple risk its buyback pace?',
  thread_id: 'thread-11',
  created_at: '2026-01-10T12:00:00Z',
}

// Stored bodies are asdict(ResearchResult): 11 fields, no thread_id /
// save_requested / report_id inside `result`.
const passBody = {
  question: 'How durable is the reported growth?',
  ticker: 'MSFT',
  company: { cik: 789019, ticker: 'MSFT', name: 'Microsoft Corporation' },
  final_answer:
    'Microsoft reported revenue of $245,122,000,000 (VERIFIED FACT revenue FY2026, accession 0000789019-26-000001), which management attributed to cloud scale (LLM INTERPRETATION).',
  citations: ['0000789019-26-000001'],
  limitations: [],
  trace: [
    'resolve: Microsoft Corporation (CIK 789019)',
    'llm#1: tool calls -> get_financial_facts, search_filing_sections',
    'validate: FAIL; 1 citation(s), 0 unsupported; 1 unverified number(s)',
    'revise #1: sending validation feedback',
    'validate: PASS; 1 citation(s), 0 unsupported',
    'finalize: 1 citation(s), 0 limitation(s)',
  ],
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
    { tool: 'get_financial_facts', args: { ticker: 'MSFT', metric: 'revenue' }, chunk_ids: [] },
    {
      tool: 'search_filing_sections',
      args: { ticker: 'MSFT', query: 'margin expansion', k: 4 },
      chunk_ids: ['0000789019-26-000001:12'],
    },
  ],
}

const failBody = {
  ...passBody,
  question: 'Does Apple risk its buyback pace?',
  ticker: 'AAPL',
  company: { cik: 320193, ticker: 'AAPL', name: 'Apple Inc.' },
  final_answer:
    'Apple repurchased $9,900,000,000 of stock in the period (VERIFIED FACT, accession 0000320193-26-000006).',
  citations: ['0000320193-26-000006'],
  limitations: [
    'removed: Rival filings cite 0000310194-26-000006 with a 999,999,999 backlog [unsupported citation(s): 0000310194-26-000006 ; unverified number(s): 999,999,999]',
  ],
  trace: [
    'resolve: Apple Inc. (CIK 320193)',
    'llm#1: tool calls -> search_filing_sections',
    'validate: FAIL; 2 citation(s), 1 unsupported; 1 unverified number(s)',
    'revise #1: sending validation feedback',
    'validate: FAIL; 2 citation(s), 1 unsupported; 1 unverified number(s)',
    'finalize: 1 citation(s), 1 limitation(s)',
  ],
  validation: {
    ok: false,
    citations: ['0000310194-26-000006', '0000320193-26-000006'],
    unsupported_citations: ['0000310194-26-000006'],
    verified_numbers: ['9,900,000,000'],
    unverified_numbers: ['999,999,999'],
    notes: [],
  },
  iterations: 4,
  revises: 3,
  tool_calls: [
    {
      tool: 'search_filing_sections',
      args: { ticker: 'AAPL', query: 'buyback pace', k: 4 },
      chunk_ids: ['0000320193-26-000006:7'],
    },
  ],
}

const detail12 = { ...summary12, result: passBody }
const detail11 = { ...summary11, result: failBody }

const page = (items, total = items.length, limit = 50, offset = 0) => ({ items, total, limit, offset })

const setup = (list) => {
  getReports.mockResolvedValue(list ?? page([summary12, summary11]))
  getReport.mockImplementation((id) => Promise.resolve(id === 11 ? detail11 : detail12))
}

const panelWithTitle = (title) => screen.getByText(title).closest('.panel')

// The empty state links to /research, so the page renders inside a router.
const renderPage = () => render(
  <MemoryRouter>
    <ReportsPage />
  </MemoryRouter>,
)

describe('Reports page', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('loads the list, auto-selects the newest report, and renders real row fields', async () => {
    setup()
    renderPage()

    // Both columns show their loading state first.
    expect(await screen.findAllByText('Loading saved reports')).toHaveLength(2)
    expect(getReports).toHaveBeenCalledWith({ limit: 50, offset: 0, signal: expect.any(AbortSignal) })

    const list = panelWithTitle('Saved research reports')
    expect(await within(list).findByText('How durable is the reported growth?')).toBeTruthy()
    expect(within(list).getByText('1–2 of 2')).toBeTruthy()
    expect(within(list).getByRole('button', { name: 'Next page' }).disabled).toBe(true)

    // Newest row first (server order), auto-selected; only summary fields.
    const rows = within(list).getAllByRole('button').filter((button) => button.hasAttribute('aria-pressed'))
    expect(rows).toHaveLength(2)
    expect(rows[0].getAttribute('aria-pressed')).toBe('true')
    expect(rows[1].getAttribute('aria-pressed')).toBe('false')
    expect(within(rows[0]).getByText('Report #12')).toBeTruthy()
    expect(within(rows[0]).getByText('MSFT')).toBeTruthy()
    expect(within(rows[0]).getByText('Saved Jan 15, 2026')).toBeTruthy()

    // The newest report's detail loads automatically with the abort signal.
    expect(await screen.findByText('Report #12 · MSFT')).toBeTruthy()
    expect(getReport).toHaveBeenCalledWith(12, { signal: expect.any(AbortSignal) })
  })

  it('shows the empty list with a start-research action and no detail fetch', async () => {
    setup(page([]))
    renderPage()

    expect(await screen.findByText('No saved research yet')).toBeTruthy()
    expect(screen.getByText('Completed research appears here when it is saved as a report.')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Start research' }).getAttribute('href')).toBe('/research')
    expect(screen.getByText('No report selected')).toBeTruthy()
    expect(getReport).not.toHaveBeenCalled()
  })

  it('shows the shared list error in both columns and recovers through retry', async () => {
    setup()
    getReports.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'boom' } } })
    renderPage()

    // List panel and detail column both mirror the failure.
    const alerts = await screen.findAllByRole('alert')
    expect(alerts).toHaveLength(2)
    expect(alerts[0].textContent).toContain('The backend encountered a problem. Please try again later.')
    expect(screen.queryByText(/boom/)).toBeNull()

    fireEvent.click(screen.getAllByRole('button', { name: 'Try again' })[0])

    const list = panelWithTitle('Saved research reports')
    expect(await within(list).findByText('How durable is the reported growth?')).toBeTruthy()
    expect(getReports).toHaveBeenCalledTimes(2)
  })

  it('paginates through the list endpoint with disabled ends', async () => {
    setup(page([summary12], 51, 50, 0))
    renderPage()

    const list = panelWithTitle('Saved research reports')
    expect(await within(list).findByText('1–1 of 51')).toBeTruthy()
    expect(within(list).getByRole('button', { name: 'Previous page' }).disabled).toBe(true)
    expect(within(list).getByRole('button', { name: 'Next page' }).disabled).toBe(false)

    getReports.mockResolvedValueOnce(page([summary11], 51, 50, 51))
    fireEvent.click(within(list).getByRole('button', { name: 'Next page' }))

    expect(await within(list).findByText('51–51 of 51')).toBeTruthy()
    expect(getReports).toHaveBeenLastCalledWith({ limit: 50, offset: 50, signal: expect.any(AbortSignal) })
    expect(within(list).getByRole('button', { name: 'Next page' }).disabled).toBe(true)
    expect(within(list).getByRole('button', { name: 'Previous page' }).disabled).toBe(false)
  })

  it('shows the detail loading state until the report arrives', async () => {
    setup()
    getReport.mockReturnValue(new Promise(() => {}))
    renderPage()

    const list = panelWithTitle('Saved research reports')
    await within(list).findByText('How durable is the reported growth?')

    // The detail fetch starts in an effect after the list commits.
    await waitFor(() => expect(getReport).toHaveBeenCalledWith(12, { signal: expect.any(AbortSignal) }))
    expect(screen.getByText('Loading report')).toBeTruthy()
    expect(screen.queryByText('Evidence-validated answer')).toBeNull()
  })

  it('renders every evidence section of a stored passing report', async () => {
    setup()
    renderPage()
    expect(await screen.findByText('Evidence-validated answer')).toBeTruthy()

    // Header: report id, ticker, question, saved instant in UTC, CIK.
    const header = panelWithTitle('Report #12 · MSFT')
    expect(within(header).getByText('Saved as report #12')).toBeTruthy()
    expect(within(header).getByText('How durable is the reported growth?')).toBeTruthy()
    expect(within(header).getByText(/Saved Jan 15, 2026, 11:30 PM/)).toBeTruthy()
    expect(within(header).getByText(/CIK 0000789019/)).toBeTruthy()

    // Answer panel: provenance label, the text itself, the save line.
    const answer = panelWithTitle('Evidence-validated answer')
    expect(within(answer).getByText('LLM-generated · evidence-validated')).toBeTruthy()
    expect(within(answer).getByText('MSFT · Microsoft Corporation')).toBeTruthy()
    expect(within(answer).getByText(passBody.final_answer)).toBeTruthy()
    expect(within(answer).getByText('Saved as report #12')).toBeTruthy()

    // Validation: PASS verdict and the populated bucket only.
    const validation = panelWithTitle('Deterministic validation')
    expect(within(validation).getByText('PASS')).toBeTruthy()
    expect(within(validation).getByText(/PASS — every citation/)).toBeTruthy()
    expect(within(validation).getByText('Verified numbers')).toBeTruthy()
    expect(within(validation).getByText('245,122,000,000')).toBeTruthy()
    expect(within(validation).queryByText('Unverified numbers')).toBeNull()
    expect(within(validation).queryByText('Unsupported citations')).toBeNull()

    // Citations as plain accession text, no links.
    const citations = panelWithTitle('Accessions cited in the answer')
    expect(within(citations).getByText('0000789019-26-000001')).toBeTruthy()
    expect(citations.querySelector('a')).toBeNull()

    // Tool calls with arguments and chunk ids.
    const tools = panelWithTitle('Tool calls this run')
    expect(within(tools).getByText('get_financial_facts')).toBeTruthy()
    expect(within(tools).getByText('ticker: MSFT · metric: revenue')).toBeTruthy()
    expect(within(tools).getByText(/0000789019-26-000001:12/)).toBeTruthy()

    // Limitations and the collapsible audit trace.
    const limitations = panelWithTitle('What this answer omits')
    expect(within(limitations).getByText('Nothing was removed from the draft for this run.')).toBeTruthy()
    expect(screen.getByText(/2 iteration\(s\), 1 revision\(s\)/)).toBeTruthy()
    expect(screen.getByText(/validate: PASS; 1 citation\(s\), 0 unsupported/)).toBeTruthy()
  })

  it('renders a stored failed validation with its buckets and recorded removals', async () => {
    setup(page([summary11]))
    renderPage()
    expect(await screen.findByText('Evidence-validated answer')).toBeTruthy()

    const validation = panelWithTitle('Deterministic validation')
    expect(within(validation).getByText('FAIL')).toBeTruthy()
    expect(within(validation).getByText(/FAIL — the answer could not be fully verified/)).toBeTruthy()
    expect(within(validation).getByText('Verified numbers')).toBeTruthy()
    expect(within(validation).getByText('9,900,000,000')).toBeTruthy()
    expect(within(validation).getByText('Unverified numbers')).toBeTruthy()
    expect(within(validation).getByText('999,999,999')).toBeTruthy()
    expect(within(validation).getByText('Unsupported citations')).toBeTruthy()
    expect(within(validation).getByText('0000310194-26-000006')).toBeTruthy()
    // Empty buckets stay hidden even on a failure; the verdict never hides.
    expect(within(validation).queryByText('Notes')).toBeNull()

    const answer = panelWithTitle('Evidence-validated answer')
    expect(within(answer).getByText(/Apple repurchased/)).toBeTruthy()
    expect(within(answer).getByText('Saved as report #11')).toBeTruthy()
    expect(screen.getByText(/removed: Rival filings cite 0000310194-26-000006/)).toBeTruthy()
  })

  it('shows a static not-found state for a removed report without raw detail', async () => {
    setup()
    getReport.mockRejectedValueOnce({ response: { status: 404, data: { detail: 'Report not found.' } } })
    renderPage()

    expect(await screen.findByText('Report not found')).toBeTruthy()
    expect(screen.getByText(/no longer available/)).toBeTruthy()
    // The raw backend detail is never rendered, and 404 offers no retry.
    expect(screen.queryByText('Report not found.')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })

  it('shows the shared error for a failed detail load and recovers through retry', async () => {
    setup()
    getReport.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'boom' } } })
    renderPage()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('The backend encountered a problem. Please try again later.')).toBeTruthy()
    expect(screen.queryByText(/boom/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Evidence-validated answer')).toBeTruthy()
    expect(getReport).toHaveBeenCalledTimes(2)
  })

  it('shows the shared malformed-response message for an invalid detail', async () => {
    setup()
    getReport.mockRejectedValueOnce(
      Object.assign(new Error('bad shape'), { name: 'InvalidApiResponseError' }),
    )
    renderPage()

    expect(await screen.findByText('The backend returned an unexpected response. Please try again.')).toBeTruthy()
    expect(screen.queryByText(/bad shape/)).toBeNull()
  })

  it('switches the detail and the pressed row when another report is selected', async () => {
    setup()
    renderPage()
    expect(await screen.findByText('Report #12 · MSFT')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /Does Apple risk its buyback pace/ }))

    expect(await screen.findByText('Report #11 · AAPL')).toBeTruthy()
    expect(getReport).toHaveBeenLastCalledWith(11, { signal: expect.any(AbortSignal) })
    const answer = panelWithTitle('Evidence-validated answer')
    expect(within(answer).getByText('Does Apple risk its buyback pace?')).toBeTruthy()

    const row12 = screen.getByRole('button', { name: /How durable is the reported growth/ })
    const row11 = screen.getByRole('button', { name: /Does Apple risk its buyback pace/ })
    expect(row12.getAttribute('aria-pressed')).toBe('false')
    expect(row11.getAttribute('aria-pressed')).toBe('true')
  })

  it('keeps list rows to real summary fields with no validation or provenance claims', async () => {
    setup()
    renderPage()

    const list = panelWithTitle('Saved research reports')
    await within(list).findByText('How durable is the reported growth?')

    // The summary schema carries no validation verdict, thread id or evidence.
    expect(within(list).queryByText('PASS')).toBeNull()
    expect(within(list).queryByText('FAIL')).toBeNull()
    expect(within(list).queryByText('thread-12')).toBeNull()
    expect(within(list).queryByText('0000789019-26-000001')).toBeNull()
    expect(within(list).queryByRole('button', { name: /delete|edit|remove/i })).toBeNull()
    expect(document.querySelector('a[href*="sec.gov"]')).toBeNull()
  })

  it('keeps the stored thread id internal on the detail view', async () => {
    setup()
    renderPage()
    expect(await screen.findByText('Evidence-validated answer')).toBeTruthy()

    // The detail response carries thread_id at the top level; it is validated
    // but never surfaced, and no continuation affordance exists.
    expect(screen.queryByText('thread-12')).toBeNull()
    expect(screen.queryByText(/thread/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /follow.?up|continuation|resume/i })).toBeNull()
  })
})
