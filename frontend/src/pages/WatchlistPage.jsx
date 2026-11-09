/**
 * WatchlistPage.jsx — Display and manage the user's watchlist.
 *
 * Layout:
 *  - PageIntro with an "Add company" toggle button.
 *  - Optional add-company form panel (a <select> populated from getCompanies).
 *  - Main panel: table of watchlist entries (ticker + CIK + date added + remove).
 *  - Pagination below the table.
 *
 * Data sources:
 *  - getWatchlist (read)  — from overview.js (already exists there).
 *  - getCompanies (read)  — from overview.js, used to populate the add form.
 *  - addWatchlistEntry    — from watchlist.js (POST /watchlist).
 *  - removeWatchlistEntry — from watchlist.js (DELETE /watchlist/{ticker}).
 *
 * Error-handling strategy:
 *  - List load errors → ErrorState with retry.
 *  - Add 409 (duplicate) → specific inline message below the form.
 *  - Add 404 (unknown company) → "Company not found" inline message.
 *  - Other add errors → getApiErrorMessage fallback.
 *  - Remove errors → per-row inline message so the rest of the list stays visible.
 *  - Raw backend detail strings are NEVER rendered in any error state.
 */

import { Fragment, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { getCompanies, getWatchlist } from '../api/overview.js'
import { addWatchlistEntry, removeWatchlistEntry } from '../api/watchlist.js'
import { getApiErrorMessage } from '../api/client.js'
import { useLoad } from '../hooks/useLoad.js'
import {
  Button,
  DataTable,
  EmptyState,
  ErrorState,
  LinkButton,
  LoadingState,
  PageIntro,
  Pagination,
  Panel,
} from '../components/ui.jsx'
import './WatchlistPage.css'

// Number of watchlist rows per page.
const pageSize = 50

/**
 * Format a UTC ISO date string as a short human-readable date.
 * Returns "Not recorded" for missing or invalid values.
 * Always interprets in UTC so a stored instant reads identically everywhere.
 */
function dateLabel(value) {
  if (!value) return 'Not recorded'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Not recorded'
  return new Intl.DateTimeFormat('en', {
    timeZone: 'UTC',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date)
}

/**
 * Translate a mutation error into a watchlist-specific message.
 *
 * The backend returns structured JSON errors; we map status codes to static,
 * user-safe strings — raw backend `detail` values are never shown.
 *
 * @param {unknown} error
 * @param {'add' | 'remove'} operation
 * @returns {string}
 */
function watchlistErrorMessage(error, operation) {
  const status = error?.response?.status
  if (operation === 'add') {
    if (status === 409) return 'This company is already on your watchlist.'
    if (status === 404) return 'Company not found. Check the ticker and try again.'
  }
  if (operation === 'remove') {
    if (status === 404) return 'This entry is no longer on the watchlist. Refresh to update the list.'
  }
  // Fall back to the shared client-side message map (no raw backend text).
  return getApiErrorMessage(error) ?? 'Something went wrong. Please try again.'
}

// ---------------------------------------------------------------------------
// WatchlistPage
// ---------------------------------------------------------------------------

function WatchlistPage() {
  // ---- pagination & reload -------------------------------------------------
  const [reload, setReload] = useState(0)
  const [offset, setOffset] = useState(0)

  // ---- add-form state ------------------------------------------------------
  const [formOpen, setFormOpen] = useState(false)
  const [companiesAttempt, setCompaniesAttempt] = useState(0)
  const [companyOffset, setCompanyOffset] = useState(0)
  const [selectedTicker, setSelectedTicker] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')

  // ---- per-row remove state ------------------------------------------------
  // Maps ticker → error string for in-progress or failed removes.
  const [removingTickers, setRemovingTickers] = useState(new Set())
  const [removeErrors, setRemoveErrors] = useState({}) // ticker → message

  // ---- watchlist list load -------------------------------------------------
  const listState = useLoad(
    (signal) => getWatchlist({ limit: pageSize, offset, signal }),
    [offset, reload],
  )
  const items = listState.status === 'ready' ? listState.data.items : []

  // ---- companies load (for the add form) -----------------------------------
  // Always loaded so it's ready when the user opens the form.
  const companiesState = useLoad(
    (signal) => getCompanies({ limit: pageSize, offset: companyOffset, signal }),
    [companiesAttempt, companyOffset],
  )
  const companies = companiesState.status === 'ready' ? companiesState.data.items : []

  // ---- handlers ------------------------------------------------------------

  function openForm() {
    setFormOpen(true)
    setSelectedTicker('')
    setSubmitError('')
  }

  function closeForm() {
    setFormOpen(false)
    setSelectedTicker('')
    setSubmitError('')
  }

  async function handleAdd(event) {
    event.preventDefault()
    if (!selectedTicker || !companies.some((company) => company.ticker === selectedTicker)) {
      setSelectedTicker('')
      setSubmitError('Select a company to add.')
      return
    }
    setSubmitting(true)
    setSubmitError('')
    try {
      await addWatchlistEntry(selectedTicker)
      closeForm()
      setOffset(0)
      setReload((v) => v + 1)
    } catch (error) {
      setSubmitError(watchlistErrorMessage(error, 'add'))
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRemove(ticker) {
    // Prevent double-clicks while the request is in flight.
    if (removingTickers.has(ticker)) return
    setRemovingTickers((prev) => new Set([...prev, ticker]))
    setRemoveErrors((prev) => { const next = { ...prev }; delete next[ticker]; return next })
    try {
      await removeWatchlistEntry(ticker)
      const totalAfterRemoval = listState.data.total - 1
      if (items.length === 1) {
        setOffset(totalAfterRemoval > 0 ? Math.max(0, offset - pageSize) : 0)
      }
      setReload((v) => v + 1)
    } catch (error) {
      setRemoveErrors((prev) => ({ ...prev, [ticker]: watchlistErrorMessage(error, 'remove') }))
    } finally {
      setRemovingTickers((prev) => {
        const next = new Set(prev)
        next.delete(ticker)
        return next
      })
    }
  }

  function changeCompanyPage(nextOffset) {
    setSelectedTicker('')
    setSubmitError('')
    setCompanyOffset(nextOffset)
  }

  // ---- render --------------------------------------------------------------

  return (
    <div className="watchlist-page" data-testid="watchlist-page">
      <PageIntro
        eyebrow="COMPANY WATCHLIST"
        title="Watchlist"
        description="Companies you are tracking. Add a company to monitor its filings and financial data alongside your investment theses."
        action={(
          <Button onClick={formOpen ? closeForm : openForm}>
            {formOpen ? 'Cancel' : 'Add company'}
          </Button>
        )}
      />

      {/* ---- add-company form ---- */}
      {formOpen && (
        <Panel eyebrow="ADD COMPANY" title="Add a company to your watchlist">
          {companiesState.status === 'loading' && (
            <LoadingState label="Loading companies" />
          )}
          {companiesState.status === 'error' && (
            <ErrorState
              message={companiesState.error}
              onRetry={() => setCompaniesAttempt((v) => v + 1)}
            />
          )}
          {companiesState.status === 'ready' && (
            <>
              <form className="watchlist-add-form" onSubmit={handleAdd} noValidate>
                <label className="watchlist-field" htmlFor="watchlist-company-select">
                  <span>Company</span>
                  <select
                    id="watchlist-company-select"
                    value={selectedTicker}
                    onChange={(e) => setSelectedTicker(e.target.value)}
                    aria-label="Select company"
                    disabled={submitting}
                  >
                    <option value="">— choose a company —</option>
                    {companies.map((company) => (
                      <option key={company.ticker} value={company.ticker}>
                        {company.ticker}
                        {' '}
                        (CIK
                        {' '}
                        {String(company.cik).padStart(10, '0')}
                        )
                      </option>
                    ))}
                  </select>
                </label>

                {submitError && (
                  <p className="watchlist-field__error" role="alert">{submitError}</p>
                )}

                <div className="watchlist-form__actions">
                  <Button type="submit" disabled={submitting}>
                    {submitting ? 'Adding…' : 'Add to watchlist'}
                  </Button>
                  <Button type="button" variant="secondary" onClick={closeForm} disabled={submitting}>
                    Cancel
                  </Button>
                </div>
              </form>
              {companiesState.data.total > companiesState.data.items.length && (
                <Pagination
                  page={companiesState.data}
                  offset={companyOffset}
                  setOffset={changeCompanyPage}
                  className="watchlist-pagination watchlist-company-pagination"
                />
              )}
            </>
          )}
        </Panel>
      )}

      {/* ---- watchlist table ---- */}
      <Panel eyebrow="TRACKED COMPANIES" title="Your watchlist">
        {listState.status === 'loading' && (
          <LoadingState label="Loading watchlist" />
        )}
        {listState.status === 'error' && (
          <ErrorState
            message={listState.error}
            onRetry={() => { setOffset(0); setReload((v) => v + 1) }}
          />
        )}
        {listState.status === 'ready' && items.length === 0 && (
          <EmptyState
            title="No companies on the watchlist"
            detail="Add a company using the button above to start monitoring its filings."
            action={<LinkButton to="/companies">Browse companies</LinkButton>}
          />
        )}
        {listState.status === 'ready' && items.length > 0 && (
          <>
            <DataTable className="watchlist-table">
              <thead>
                <tr>
                  <th scope="col">Ticker</th>
                  <th scope="col">CIK</th>
                  <th scope="col">Added</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((entry) => (
                  <Fragment key={entry.ticker}>
                    <tr className="watchlist-row">
                      <td className="watchlist-row__ticker">{entry.ticker}</td>
                      <td className="watchlist-row__cik">
                        {String(entry.cik).padStart(10, '0')}
                      </td>
                      <td className="watchlist-row__date">
                        {dateLabel(entry.created_at)}
                      </td>
                      <td className="watchlist-row__action">
                        <button
                          type="button"
                          className="watchlist-remove-btn"
                          aria-label={`Remove ${entry.ticker} from watchlist`}
                          disabled={removingTickers.has(entry.ticker)}
                          onClick={() => handleRemove(entry.ticker)}
                        >
                          <Trash2 size={14} aria-hidden="true" />
                        </button>
                      </td>
                    </tr>
                    {removeErrors[entry.ticker] && (
                      <tr key={`${entry.ticker}-error`} className="watchlist-row-error">
                        <td colSpan={4}>
                          <p className="watchlist-field__error" role="alert">
                            {removeErrors[entry.ticker]}
                          </p>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </DataTable>
            <Pagination
              page={listState.data}
              offset={offset}
              setOffset={setOffset}
              className="watchlist-pagination"
            />
          </>
        )}
      </Panel>
    </div>
  )
}

export default WatchlistPage
