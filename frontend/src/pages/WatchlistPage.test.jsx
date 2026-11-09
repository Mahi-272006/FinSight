import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// vi.mock must precede imports — Vitest hoists these declarations.
vi.mock('../api/overview.js', () => ({
  getWatchlist: vi.fn(),
  getCompanies: vi.fn(),
}))
vi.mock('../api/watchlist.js', () => ({
  addWatchlistEntry: vi.fn(),
  removeWatchlistEntry: vi.fn(),
}))

import { getCompanies, getWatchlist } from '../api/overview.js'
import { addWatchlistEntry, removeWatchlistEntry } from '../api/watchlist.js'
import WatchlistPage from './WatchlistPage.jsx'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

// Late-evening UTC — a local-time implementation would show a different
// calendar day on most machines.  UTC rendering pins a single expected string.
const timestamp = '2026-11-01T23:00:00Z'

const entryAAPL = { cik: 320193, ticker: 'AAPL', created_at: timestamp }
const entryMSFT = { cik: 789019, ticker: 'MSFT', created_at: '2026-10-20T10:00:00Z' }

const companyAAPL = { cik: 320193, ticker: 'AAPL' }
const companyMSFT = { cik: 789019, ticker: 'MSFT' }

const page = (items, total = items.length, limit = 50, offset = 0) => ({
  items, total, limit, offset,
})

// Axios-style error helper — matches what the page receives from the API layer.
const axiosErr = (status, detail = 'backend detail') => ({
  response: { status, data: { detail } },
})

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

function setupDefault() {
  getWatchlist.mockResolvedValue(page([entryAAPL, entryMSFT]))
  getCompanies.mockResolvedValue(page([companyAAPL, companyMSFT]))
}

// Render inside a router because EmptyState action uses <LinkButton to="…">.
const renderPage = () => render(
  <MemoryRouter>
    <WatchlistPage />
  </MemoryRouter>,
)

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('WatchlistPage', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  // -------------------------------------------------------------------------
  // 1. Loading then table rows
  // -------------------------------------------------------------------------

  it('shows loading state then renders ticker, CIK and added date for each row', async () => {
    setupDefault()
    renderPage()

    // Initial loading label is visible.
    expect(screen.getByText('Loading watchlist')).toBeTruthy()

    // After data resolves, the table rows appear.
    expect(await screen.findByText('AAPL')).toBeTruthy()
    expect(screen.getByText('MSFT')).toBeTruthy()

    // CIK values are zero-padded to 10 digits.
    expect(screen.getByText('0000320193')).toBeTruthy()
    expect(screen.getByText('0000789019')).toBeTruthy()

    // Date is formatted in UTC (Nov 1 2026 — not Oct 31 on any local timezone).
    expect(screen.getByText('Nov 1, 2026')).toBeTruthy()
    expect(screen.getByText('Oct 20, 2026')).toBeTruthy()
  })

  // -------------------------------------------------------------------------
  // 2. Empty state
  // -------------------------------------------------------------------------

  it('shows the empty state with correct text when the watchlist has no entries', async () => {
    getWatchlist.mockResolvedValue(page([]))
    getCompanies.mockResolvedValue(page([]))
    renderPage()

    expect(await screen.findByText('No companies on the watchlist')).toBeTruthy()
    expect(screen.getByText(/Add a company using the button above/)).toBeTruthy()
    // Empty state action links to /companies.
    expect(screen.getByRole('link', { name: /Browse companies/i })).toBeTruthy()
  })

  // -------------------------------------------------------------------------
  // 3. Error state with retry
  // -------------------------------------------------------------------------

  it('shows the error state and retries on button click', async () => {
    getWatchlist.mockRejectedValueOnce(axiosErr(500))
    getCompanies.mockResolvedValue(page([]))
    renderPage()

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('The backend encountered a problem. Please try again later.')).toBeTruthy()

    // Recover on retry.
    getWatchlist.mockResolvedValueOnce(page([entryAAPL]))
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('AAPL')).toBeTruthy()
  })

  // -------------------------------------------------------------------------
  // 4. Pagination controls
  // -------------------------------------------------------------------------

  it('renders pagination and disables previous on first page', async () => {
    getWatchlist.mockResolvedValue(page([entryAAPL, entryMSFT], 60, 50, 0))
    getCompanies.mockResolvedValue(page([]))
    renderPage()

    await screen.findByText('AAPL')

    const prev = screen.getByRole('button', { name: 'Previous page' })
    const next = screen.getByRole('button', { name: 'Next page' })
    expect(prev.disabled).toBe(true)
    expect(next.disabled).toBe(false)

    // Page counter text: "1–2 of 60"
    expect(screen.getByText('1–2 of 60')).toBeTruthy()
  })

  it('disables next on the last page', async () => {
    // Navigate to offset 50, then verify 50+2 >= 52 disables next.
    getWatchlist
      .mockResolvedValueOnce(page([entryAAPL, entryMSFT], 60, 50, 0))
      .mockResolvedValueOnce(page([entryAAPL, entryMSFT], 52, 50, 50))
    getCompanies.mockResolvedValue(page([]))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(getWatchlist).toHaveBeenCalledTimes(2))

    const next = screen.getByRole('button', { name: 'Next page' })
    expect(next.disabled).toBe(true)
  })

  // -------------------------------------------------------------------------
  // 5. Add button opens form
  // -------------------------------------------------------------------------

  it('opens the add-company form when "Add company" is clicked', async () => {
    setupDefault()
    renderPage()

    await screen.findByText('AAPL')

    // Form is not visible initially.
    expect(screen.queryByText('Add a company to your watchlist')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))

    expect(await screen.findByText('Add a company to your watchlist')).toBeTruthy()
  })

  // -------------------------------------------------------------------------
  // 6. Form populates company selector from getCompanies
  // -------------------------------------------------------------------------

  it('populates the company selector from getCompanies when the form is open', async () => {
    setupDefault()
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))

    // Form loads and select options appear.
    const select = await screen.findByRole('combobox', { name: 'Select company' })
    expect(within(select).getByText(/AAPL/)).toBeTruthy()
    expect(within(select).getByText(/MSFT/)).toBeTruthy()
  })

  // -------------------------------------------------------------------------
  // 7. Form shows loading while companies load
  // -------------------------------------------------------------------------

  it('shows the companies loading state inside the form before they resolve', async () => {
    // Never resolves during this test — simulates in-flight companies request.
    getWatchlist.mockResolvedValue(page([entryAAPL]))
    getCompanies.mockReturnValue(new Promise(() => {}))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))

    expect(await screen.findByText('Loading companies')).toBeTruthy()
  })

  // -------------------------------------------------------------------------
  // 8. Form shows error if getCompanies fails
  // -------------------------------------------------------------------------

  it('shows an error state inside the form when getCompanies fails', async () => {
    getWatchlist.mockResolvedValue(page([entryAAPL]))
    getCompanies.mockRejectedValue(axiosErr(503))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))

    expect(await screen.findByText('The service is temporarily unavailable. Please try again later.')).toBeTruthy()
  })

  it('retries loading companies from the add form', async () => {
    getWatchlist.mockResolvedValue(page([entryAAPL]))
    getCompanies.mockRejectedValueOnce(axiosErr(503)).mockResolvedValueOnce(page([companyAAPL]))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))
    expect(await screen.findByText('The service is temporarily unavailable. Please try again later.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('combobox', { name: 'Select company' })).toBeTruthy()
    expect(getCompanies).toHaveBeenCalledTimes(2)
  })

  it('pages through companies beyond the first API page', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({
      cik: 100000 + index,
      ticker: `T${index}`,
    }))
    const secondPage = [{ cik: 789019, ticker: 'MSFT' }]
    getWatchlist.mockResolvedValue(page([entryAAPL]))
    getCompanies
      .mockResolvedValueOnce(page(firstPage, 51, 50, 0))
      .mockResolvedValueOnce(page(secondPage, 51, 50, 50))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))
    const select = await screen.findByRole('combobox', { name: 'Select company' })
    expect(within(select).queryByText(/MSFT/)).toBeNull()
    fireEvent.click(within(document.querySelector('.watchlist-company-pagination'))
      .getByRole('button', { name: 'Next page' }))

    const nextSelect = await screen.findByRole('combobox', { name: 'Select company' })
    expect(within(nextSelect).getByText(/MSFT/)).toBeTruthy()
    await waitFor(() => expect(getCompanies).toHaveBeenLastCalledWith({
      limit: 50,
      offset: 50,
      signal: expect.any(AbortSignal),
    }))
  })

  it('clears the picker selection when changing pages so a hidden ticker cannot be submitted', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({
      cik: 100000 + index,
      ticker: `T${index}`,
    }))
    getWatchlist.mockResolvedValue(page([entryAAPL]))
    getCompanies
      .mockResolvedValueOnce(page(firstPage, 51, 50, 0))
      .mockResolvedValueOnce(page([companyMSFT], 51, 50, 50))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))
    const select = await screen.findByRole('combobox', { name: 'Select company' })
    fireEvent.change(select, { target: { value: 'T0' } })
    expect(select.value).toBe('T0')

    fireEvent.click(within(document.querySelector('.watchlist-company-pagination'))
      .getByRole('button', { name: 'Next page' }))

    const nextSelect = await screen.findByRole('combobox', { name: 'Select company' })
    await waitFor(() => expect(nextSelect.value).toBe(''))
    fireEvent.click(screen.getByRole('button', { name: 'Add to watchlist' }))

    expect(await screen.findByText('Select a company to add.')).toBeTruthy()
    expect(addWatchlistEntry).not.toHaveBeenCalled()
  })

  it('enforces previous and last-page boundaries in the company picker', async () => {
    const companyPage = (offset, count) => page(
      Array.from({ length: count }, (_, index) => ({
        cik: 100000 + offset + index,
        ticker: `C${offset + index}`,
      })),
      101,
      50,
      offset,
    )
    getWatchlist.mockResolvedValue(page([entryAAPL]))
    getCompanies.mockImplementation(({ offset }) => Promise.resolve(
      companyPage(offset, offset === 100 ? 1 : 50),
    ))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))
    const pagination = () => within(document.querySelector('.watchlist-company-pagination'))
    await screen.findByRole('combobox', { name: 'Select company' })
    expect(pagination().getByRole('button', { name: 'Previous page' }).disabled).toBe(true)
    expect(pagination().getByRole('button', { name: 'Next page' }).disabled).toBe(false)

    fireEvent.click(pagination().getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(getCompanies).toHaveBeenLastCalledWith({
      limit: 50, offset: 50, signal: expect.any(AbortSignal),
    }))
    expect(pagination().getByRole('button', { name: 'Previous page' }).disabled).toBe(false)

    fireEvent.click(pagination().getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(getCompanies).toHaveBeenLastCalledWith({
      limit: 50, offset: 100, signal: expect.any(AbortSignal),
    }))
    expect(pagination().getByRole('button', { name: 'Next page' }).disabled).toBe(true)

    fireEvent.click(pagination().getByRole('button', { name: 'Previous page' }))
    await waitFor(() => expect(getCompanies).toHaveBeenLastCalledWith({
      limit: 50, offset: 50, signal: expect.any(AbortSignal),
    }))
  })

  // -------------------------------------------------------------------------
  // 9. Add success: form closes, list refreshes
  // -------------------------------------------------------------------------

  it('closes the form and reloads the list after a successful add', async () => {
    setupDefault()
    addWatchlistEntry.mockResolvedValue(entryAAPL)
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))

    const select = await screen.findByRole('combobox', { name: 'Select company' })
    fireEvent.change(select, { target: { value: 'AAPL' } })

    fireEvent.submit(screen.getByRole('button', { name: 'Add to watchlist' }).closest('form'))

    await waitFor(() => expect(addWatchlistEntry).toHaveBeenCalledWith('AAPL'))

    // Form closes after success.
    await waitFor(() => {
      expect(screen.queryByText('Add a company to your watchlist')).toBeNull()
    })

    // List is reloaded (getWatchlist called at least twice: initial + after add).
    await waitFor(() => expect(getWatchlist).toHaveBeenCalledTimes(2))
  })

  // -------------------------------------------------------------------------
  // 10. Add 409: inline "already on the watchlist" message
  // -------------------------------------------------------------------------

  it('shows a specific duplicate message on 409 without closing the form', async () => {
    setupDefault()
    addWatchlistEntry.mockRejectedValue(axiosErr(409, 'Company is already on the watchlist.'))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))

    const select = await screen.findByRole('combobox', { name: 'Select company' })
    fireEvent.change(select, { target: { value: 'AAPL' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add to watchlist' }))

    // Page-defined message is shown — raw backend detail is NOT rendered.
    expect(await screen.findByText('This company is already on your watchlist.')).toBeTruthy()
    expect(screen.queryByText('Company is already on the watchlist.')).toBeNull()

    // Form stays open.
    expect(screen.getByText('Add a company to your watchlist')).toBeTruthy()
  })

  it('shows a safe not-found message for add-company 404 errors', async () => {
    setupDefault()
    addWatchlistEntry.mockRejectedValue(axiosErr(404, 'Company not found.'))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))
    fireEvent.change(await screen.findByRole('combobox', { name: 'Select company' }), { target: { value: 'AAPL' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add to watchlist' }))

    expect(await screen.findByText('Company not found. Check the ticker and try again.')).toBeTruthy()
    expect(screen.queryByText('Company not found.')).toBeNull()
  })

  it('shows a safe generic message for other add failures', async () => {
    setupDefault()
    addWatchlistEntry.mockRejectedValue(axiosErr(400, 'internal backend detail'))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))
    fireEvent.change(await screen.findByRole('combobox', { name: 'Select company' }), { target: { value: 'AAPL' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add to watchlist' }))

    expect(await screen.findByText('The request could not be processed. Check the information and try again.')).toBeTruthy()
    expect(screen.queryByText('internal backend detail')).toBeNull()
  })

  it('prevents duplicate add submissions while the request is pending', async () => {
    setupDefault()
    let resolveAdd
    addWatchlistEntry.mockReturnValue(new Promise((resolve) => { resolveAdd = resolve }))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))
    fireEvent.change(await screen.findByRole('combobox', { name: 'Select company' }), {
      target: { value: 'AAPL' },
    })
    const addButton = screen.getByRole('button', { name: 'Add to watchlist' })
    fireEvent.click(addButton)
    fireEvent.click(addButton)

    expect(addWatchlistEntry).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Adding…' }).disabled).toBe(true)

    resolveAdd(entryAAPL)
    await waitFor(() => expect(getWatchlist).toHaveBeenCalledTimes(2))
    expect(addWatchlistEntry).toHaveBeenCalledTimes(1)
  })

  // -------------------------------------------------------------------------
  // 11. Client-side guard: cannot submit with no company selected
  // -------------------------------------------------------------------------

  it('shows a validation error and does not call the API when no company is selected', async () => {
    setupDefault()
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Add company' }))

    // Wait for companies to load, then submit without selecting anything.
    await screen.findByRole('combobox', { name: 'Select company' })
    fireEvent.click(screen.getByRole('button', { name: 'Add to watchlist' }))

    expect(screen.getByText('Select a company to add.')).toBeTruthy()
    expect(addWatchlistEntry).not.toHaveBeenCalled()
  })

  // -------------------------------------------------------------------------
  // 12. Remove success: list refreshes
  // -------------------------------------------------------------------------

  it('reloads the list after a successful remove', async () => {
    setupDefault()
    removeWatchlistEntry.mockResolvedValue(undefined)
    renderPage()

    await screen.findByText('AAPL')

    fireEvent.click(screen.getByRole('button', { name: 'Remove AAPL from watchlist' }))

    await waitFor(() => expect(removeWatchlistEntry).toHaveBeenCalledWith('AAPL'))
    // List reloaded after remove.
    await waitFor(() => expect(getWatchlist).toHaveBeenCalledTimes(2))
  })

  // -------------------------------------------------------------------------
  // 13. Remove 404: inline per-row error without hiding the list
  // -------------------------------------------------------------------------

  it('shows a per-row error on 404 without hiding the rest of the list', async () => {
    setupDefault()
    removeWatchlistEntry.mockRejectedValue(axiosErr(404, 'Watchlist entry not found.'))
    renderPage()

    await screen.findByText('AAPL')

    fireEvent.click(screen.getByRole('button', { name: 'Remove AAPL from watchlist' }))

    // Page-defined message is shown — raw backend detail is NOT rendered.
    expect(await screen.findByText(
      'This entry is no longer on the watchlist. Refresh to update the list.',
    )).toBeTruthy()
    expect(screen.queryByText('Watchlist entry not found.')).toBeNull()

    // The rest of the list stays visible.
    expect(screen.getByText('MSFT')).toBeTruthy()
  })

  it('shows a safe generic message after a remove failure', async () => {
    setupDefault()
    removeWatchlistEntry.mockRejectedValue(axiosErr(500, 'database password'))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Remove AAPL from watchlist' }))

    expect(await screen.findByText('The backend encountered a problem. Please try again later.')).toBeTruthy()
    expect(screen.queryByText('database password')).toBeNull()
    expect(screen.getByText('MSFT')).toBeTruthy()
  })

  it('prevents duplicate remove requests while one is pending', async () => {
    setupDefault()
    let resolveRemove
    removeWatchlistEntry.mockReturnValue(new Promise((resolve) => { resolveRemove = resolve }))
    renderPage()

    await screen.findByText('AAPL')
    const remove = screen.getByRole('button', { name: 'Remove AAPL from watchlist' })
    fireEvent.click(remove)
    fireEvent.click(remove)

    expect(removeWatchlistEntry).toHaveBeenCalledTimes(1)
    expect(remove.disabled).toBe(true)
    resolveRemove()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove AAPL from watchlist' }).disabled).toBe(false))
  })

  it('navigates watchlist pages and requests the new offset', async () => {
    getWatchlist.mockResolvedValue(page([entryAAPL, entryMSFT], 60, 50, 0))
    getCompanies.mockResolvedValue(page([]))
    renderPage()

    await screen.findByText('AAPL')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))

    await waitFor(() => expect(getWatchlist).toHaveBeenLastCalledWith({
      limit: 50,
      offset: 50,
      signal: expect.any(AbortSignal),
    }))
  })

  it('returns to the prior valid page after removing the only row on a later page', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({
      cik: 100000 + index,
      ticker: `T${index}`,
      created_at: timestamp,
    }))
    getWatchlist
      .mockResolvedValueOnce(page(firstPage, 51, 50, 0))
      .mockResolvedValueOnce(page([entryMSFT], 51, 50, 50))
      .mockResolvedValueOnce(page(firstPage, 50, 50, 0))
    getCompanies.mockResolvedValue(page([]))
    removeWatchlistEntry.mockResolvedValue(undefined)
    renderPage()

    await screen.findByText('T0')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await screen.findByText('MSFT')
    expect(screen.getByText('51–51 of 51')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Remove MSFT from watchlist' }))

    await screen.findByText('T0')
    expect(screen.getByText('1–50 of 50')).toBeTruthy()
    expect(screen.queryByText('No companies on the watchlist')).toBeNull()
    await waitFor(() => expect(getWatchlist).toHaveBeenLastCalledWith({
      limit: 50,
      offset: 0,
      signal: expect.any(AbortSignal),
    }))
  })

  // -------------------------------------------------------------------------
  // 14. No raw backend `detail` strings are exposed in any error state
  // -------------------------------------------------------------------------

  it('never exposes raw backend detail strings in any displayed error', async () => {
    // List load error
    getWatchlist.mockRejectedValue(axiosErr(500, 'SQL error at line 42'))
    getCompanies.mockResolvedValue(page([]))
    renderPage()

    await screen.findByRole('alert')

    expect(screen.queryByText('SQL error at line 42')).toBeNull()
    expect(screen.queryByText('backend detail')).toBeNull()
    expect(screen.getByText('The backend encountered a problem. Please try again later.')).toBeTruthy()
  })
})
