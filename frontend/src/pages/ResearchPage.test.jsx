import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/research.js', () => ({ runResearch: vi.fn() }))
vi.mock('../api/overview.js', () => ({ getCompanies: vi.fn() }))

import { runResearch } from '../api/research.js'
import { getCompanies } from '../api/overview.js'
import ResearchPage from './ResearchPage.jsx'

const timestamp = '2026-01-01T00:00:00Z'
const companies = [
  {
    cik: 789019,
    ticker: 'MSFT',
    name: 'Microsoft Corporation',
    fiscal_year_end_month: 6,
    created_at: timestamp,
    updated_at: timestamp,
  },
  {
    cik: 320193,
    ticker: 'AAPL',
    name: 'Apple Inc.',
    fiscal_year_end_month: 9,
    created_at: timestamp,
    updated_at: timestamp,
  },
]

// Shape mirrors a real run: the draft passed validation after one revision,
// so strip_unsupported removed nothing and limitations stayed empty.
const passResult = {
  question: 'How did Microsoft turn revenue growth into margin expansion?',
  ticker: 'MSFT',
  company: { cik: 789019, ticker: 'MSFT', name: 'Microsoft Corporation' },
  final_answer:
    'Microsoft reported revenue of $245,122,000,000 (VERIFIED FACT revenue FY2026, accession 0000789019-26-000001), which management attributed to cloud scale (LLM INTERPRETATION).',
  citations: ['0000789019-26-000001'],
  limitations: [],
  trace: [
    'resolve: Microsoft Corporation (CIK 789019)',
    'llm#1: tool calls -> get_financial_facts, search_filing_sections',
    'tool get_financial_facts(ticker=MSFT, metric=revenue)',
    'tool search_filing_sections(ticker=MSFT, query=margin expansion, k=4)',
    'llm#1: final answer (214 chars)',
    'validate: FAIL; 1 citation(s), 0 unsupported; 1 unverified number(s)',
    'revise #1: sending validation feedback',
    'llm#2: final answer (214 chars)',
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
  thread_id: '4e4e0d6a-6e6c-4d5f-9d2f-3a1b2c3d4e5f',
  save_requested: true,
  report_id: 42,
}

// Revisions exhausted: the draft's validation report still fails, while the
// final answer is the stripped text (only supported accessions survive) and
// every removed sentence is recorded as a limitation.
const failResult = {
  ...passResult,
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
    'revise #2: sending validation feedback',
    'revise #3: sending validation feedback',
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
  thread_id: 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
  save_requested: false,
  report_id: null,
}

const setup = () => {
  getCompanies.mockResolvedValue({ items: companies, total: companies.length, limit: 100, offset: 0 })
  runResearch.mockResolvedValue(passResult)
}

const panelWithTitle = (title) => screen.getByText(title).closest('.panel')

const fillForm = ({
  ticker = 'MSFT',
  question = 'How did Microsoft turn revenue growth into margin expansion?',
} = {}) => {
  fireEvent.change(screen.getByRole('combobox', { name: 'Company' }), { target: { value: ticker } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Question' }), { target: { value: question } })
}

const submitRun = () => fireEvent.click(screen.getByRole('button', { name: 'Run research' }))

describe('Research page', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('loads the company dropdown and never submits anything on mount', async () => {
    setup()
    render(<ResearchPage />)

    expect(screen.getByText('Loading companies…')).toBeTruthy()
    expect(await screen.findByRole('combobox', { name: 'Company' })).toBeTruthy()
    expect(getCompanies).toHaveBeenCalledWith(expect.objectContaining({ limit: 100 }))

    // Idle states and the empty counter, with no research POST on load.
    expect(screen.getByText('No research run yet')).toBeTruthy()
    expect(screen.getByText('No runs yet')).toBeTruthy()
    expect(screen.getByText('0/4000')).toBeTruthy()
    expect(screen.getByRole('checkbox').checked).toBe(true)
    await waitFor(() => expect(runResearch).not.toHaveBeenCalled())
  })

  it('blocks empty submissions with field errors and no API call', async () => {
    setup()
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })

    submitRun()

    expect(await screen.findByText('Choose a company.')).toBeTruthy()
    expect(screen.getByText('A question is required.')).toBeTruthy()
    expect(runResearch).not.toHaveBeenCalled()
  })

  it('counts characters, flags questions over 4000, and blocks the submit', async () => {
    setup()
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })

    const question = 'x'.repeat(4001)
    fillForm({ question })
    const counter = screen.getByText('4001/4000')
    expect(counter.className).toContain('research-counter--over')

    submitRun()

    expect(await screen.findByText('The question must be 4000 characters or fewer.')).toBeTruthy()
    expect(runResearch).not.toHaveBeenCalled()
  })

  it('submits the exact payload, shows the running state, then the result', async () => {
    setup()
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()

    submitRun()

    const running = screen.getByRole('status')
    expect(running.textContent).toContain('Researching MSFT')
    const busyButton = screen.getByRole('button', { name: 'Researching…' })
    expect(busyButton.disabled).toBe(true)

    await waitFor(() => expect(runResearch).toHaveBeenCalledTimes(1))
    const call = runResearch.mock.calls[0][0]
    expect(call.ticker).toBe('MSFT')
    expect(call.question).toBe('How did Microsoft turn revenue growth into margin expansion?')
    expect(call.save).toBe(true)
    expect(call.signal).toBeInstanceOf(AbortSignal)

    expect(await screen.findByText('Evidence-validated answer')).toBeTruthy()
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('button', { name: 'Run research' })).toBeTruthy()
  })

  it('renders every evidence section of a passing run', async () => {
    setup()
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()
    submitRun()
    expect(await screen.findByText('Evidence-validated answer')).toBeTruthy()

    // Answer panel: LLM provenance label, the text itself, save status.
    const answer = panelWithTitle('Evidence-validated answer')
    expect(within(answer).getByText('LLM-generated · evidence-validated')).toBeTruthy()
    expect(within(answer).getByText('MSFT · Microsoft Corporation')).toBeTruthy()
    expect(within(answer).getByText(passResult.final_answer)).toBeTruthy()
    expect(within(answer).getByText('Saved as report #42')).toBeTruthy()

    // Validation: PASS verdict and the populated bucket only.
    const validation = panelWithTitle('Deterministic validation')
    expect(within(validation).getByText('PASS')).toBeTruthy()
    expect(within(validation).getByText(/PASS — every citation/)).toBeTruthy()
    expect(within(validation).getByText('Verified numbers')).toBeTruthy()
    expect(within(validation).getByText('245,122,000,000')).toBeTruthy()
    expect(within(validation).queryByText('Unverified numbers')).toBeNull()
    expect(within(validation).queryByText('Unsupported citations')).toBeNull()
    expect(within(validation).queryByText('Notes')).toBeNull()

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

    // The run is listed in the session-only panel.
    const runs = panelWithTitle('Successful runs')
    expect(within(runs).getByText('MSFT')).toBeTruthy()
  })

  it('renders a failed validation with every populated bucket and the removals', async () => {
    setup()
    runResearch.mockResolvedValue(failResult)
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm({ ticker: 'AAPL', question: 'Does Apple risk its buyback pace?' })
    submitRun()
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

    // The answer itself survives, with the removed sentence as a limitation.
    const answer = panelWithTitle('Evidence-validated answer')
    expect(within(answer).getByText(/Apple repurchased/)).toBeTruthy()
    expect(within(answer).getByText('Not saved')).toBeTruthy()
    expect(screen.getByText(/removed: Rival filings cite 0000310194-26-000006/)).toBeTruthy()
    expect(within(panelWithTitle('Accessions cited in the answer')).getByText('0000320193-26-000006')).toBeTruthy()
  })

  it('passes save=false through when the checkbox is unchecked', async () => {
    setup()
    runResearch.mockResolvedValue({ ...passResult, save_requested: false, report_id: null })
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()

    fireEvent.click(screen.getByRole('checkbox'))
    submitRun()
    expect(await screen.findByText('Evidence-validated answer')).toBeTruthy()

    expect(runResearch.mock.calls[0][0].save).toBe(false)
    expect(screen.getByText('Not saved')).toBeTruthy()
    expect(screen.queryByText(/Saved as report/)).toBeNull()
  })

  it('explains a 404 as the ingested-companies rule and keeps the form filled', async () => {
    setup()
    runResearch.mockRejectedValueOnce({ response: { status: 404, data: { detail: 'Company not found.' } } })
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()
    submitRun()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/Only companies ingested into FinSight can be researched/)).toBeTruthy()
    expect(screen.queryByText(/Company not found/)).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Question' }).value).toBe(
      'How did Microsoft turn revenue growth into margin expansion?',
    )
    expect(screen.getByRole('combobox', { name: 'Company' }).value).toBe('MSFT')
    expect(screen.getByText('No research run yet')).toBeTruthy()
  })

  it('shows the shared static message for a generic 503', async () => {
    setup()
    runResearch.mockRejectedValueOnce({
      response: { status: 503, data: { detail: 'LLM service is temporarily unavailable. Try again later.' } },
    })
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()
    submitRun()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('The service is temporarily unavailable. Please try again later.')).toBeTruthy()
    expect(screen.queryByText(/LLM service/)).toBeNull()
  })

  it('distinguishes the report-persistence 503 without showing raw detail', async () => {
    setup()
    runResearch.mockRejectedValueOnce({
      response: { status: 503, data: { detail: 'Research completed, but report persistence failed.' } },
    })
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()
    submitRun()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/Research completed, but saving the report failed/)).toBeTruthy()
    // The known backend detail is compared, never rendered.
    expect(screen.queryByText(/report persistence failed/)).toBeNull()
    expect(screen.getByText(/Try running the question again/)).toBeTruthy()
  })

  it('explains a timeout as the client stopping, without claiming cancellation', async () => {
    setup()
    runResearch.mockRejectedValueOnce({ code: 'ECONNABORTED', message: 'timeout of 300000ms exceeded' })
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()
    submitRun()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/The client stopped waiting for this run/)).toBeTruthy()
    expect(screen.getByText(/backend may still be processing it/)).toBeTruthy()
    expect(screen.queryByText(/took too long/)).toBeNull()
    expect(screen.queryByText(/cancel/i)).toBeNull()
    expect(screen.queryByText(/timeout of 300000ms/)).toBeNull()
  })

  it('uses the shared network-error message', async () => {
    setup()
    runResearch.mockRejectedValueOnce({ isAxiosError: true, message: 'Network Error' })
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()
    submitRun()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(
      screen.getByText('FinSight could not reach the backend. Check that the API is running and try again.'),
    ).toBeTruthy()
  })

  it('uses the shared malformed-response message', async () => {
    setup()
    runResearch.mockRejectedValueOnce(Object.assign(new Error('bad shape'), { name: 'InvalidApiResponseError' }))
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()
    submitRun()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('The backend returned an unexpected response. Please try again.')).toBeTruthy()
  })

  it('recovers from a failed run through retry', async () => {
    setup()
    runResearch.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'boom' } } })
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })
    fillForm()
    submitRun()

    expect(await screen.findByText('The backend encountered a problem. Please try again later.')).toBeTruthy()
    expect(screen.queryByText(/boom/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Evidence-validated answer')).toBeTruthy()
    expect(runResearch).toHaveBeenCalledTimes(2)
  })

  it('lists session runs and lets the selection switch the displayed result', async () => {
    setup()
    runResearch
      .mockResolvedValueOnce(passResult)
      .mockResolvedValueOnce(failResult)
    render(<ResearchPage />)
    await screen.findByRole('combobox', { name: 'Company' })

    fillForm()
    submitRun()
    expect(await screen.findByText('Evidence-validated answer')).toBeTruthy()

    fillForm({ ticker: 'AAPL', question: 'Does Apple risk its buyback pace?' })
    submitRun()
    await waitFor(() => expect(runResearch).toHaveBeenCalledTimes(2))

    // The newest run is selected after its own submission.
    const answerPanel = () => panelWithTitle('Evidence-validated answer')
    expect(within(answerPanel()).getByText('Does Apple risk its buyback pace?')).toBeTruthy()

    const runs = panelWithTitle('Successful runs')
    const runButtons = within(runs).getAllByRole('button')
    expect(runButtons).toHaveLength(2)
    expect(within(runs).getByText('MSFT')).toBeTruthy()
    expect(within(runs).getByText('AAPL')).toBeTruthy()

    // Selecting the first run swaps the result panels back to it.
    fireEvent.click(runButtons[0])
    expect(within(answerPanel()).getByText('How did Microsoft turn revenue growth into margin expansion?')).toBeTruthy()
    expect(within(answerPanel()).getByText('Saved as report #42')).toBeTruthy()
    expect(within(panelWithTitle('Deterministic validation')).getByText('PASS')).toBeTruthy()
    expect(within(runs).getAllByRole('button')[0].getAttribute('aria-pressed')).toBe('true')
  })
})
