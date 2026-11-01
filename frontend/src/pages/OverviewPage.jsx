import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUpRight, ChevronRight, Clock3, FileSearch, ShieldAlert } from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  getCompanies,
  getCompanyAnomalies,
  getReports,
  getTheses,
  getWatchlist,
} from '../api/overview.js'
import { getApiErrorMessage, isRequestCanceled } from '../api/client.js'
import { DataTable, EmptyState, ErrorState, LinkButton, LoadingState, PageIntro, Panel } from '../components/ui.jsx'

const initialSection = { status: 'loading', data: null, error: '' }
const supportedTickers = new Set(['MSFT', 'AAPL', 'NVDA'])
const todayLabel = new Intl.DateTimeFormat('en', {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
  year: 'numeric',
}).format(new Date()).toUpperCase()

function dateLabel(value) {
  if (!value) return 'Date unavailable'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? 'Date unavailable'
    : new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric' }).format(date)
}

function fiscalYearEndLabel(month) {
  if (!month) return 'Fiscal year end not recorded'
  return `FY ends ${new Intl.DateTimeFormat('en', { month: 'short' }).format(new Date(2024, month - 1, 1))}`
}

function SeverityBadge({ value }) {
  return <span className={`severity-badge severity-badge--${String(value).toLowerCase()}`}>{value}</span>
}

function SectionBody({ section, emptyTitle, emptyDetail, action, onRetry }) {
  if (section.status === 'loading') return <LoadingState />
  if (section.status === 'error') return <ErrorState message={section.error} onRetry={onRetry} />
  if (!section.data?.length) return <EmptyState title={emptyTitle} detail={emptyDetail} action={action} />
  return null
}

function createInitialSections() {
  return {
    companies: { ...initialSection },
    watchlist: { ...initialSection },
    reports: { ...initialSection },
    theses: { ...initialSection },
    anomalies: { ...initialSection },
  }
}

function createErrorSections(error) {
  const message = getApiErrorMessage(error)
  return Object.fromEntries(
    Object.keys(createInitialSections()).map((key) => [key, {
      status: 'error', data: null, error: message,
    }]),
  )
}

async function loadOverviewSections(signal) {
  const results = await Promise.allSettled([
    getCompanies({ limit: 20, signal }),
    getWatchlist({ limit: 8, signal }),
    getReports({ limit: 5, signal }),
    getTheses({ limit: 5, signal }),
  ])
  if (signal?.aborted) return null

  const [companiesResult, watchlistResult, reportsResult, thesesResult] = results
  const next = {
    companies: resultSection(companiesResult),
    watchlist: resultSection(watchlistResult),
    reports: resultSection(reportsResult),
    theses: resultSection(thesesResult),
    anomalies: { status: 'loading', data: null, error: '' },
  }

  if (companiesResult.status === 'rejected') {
    next.anomalies = { status: 'error', data: null, error: getApiErrorMessage(companiesResult.reason) }
    return next
  }

  const supportedCompanies = companiesResult.value.items
    .filter((company) => supportedTickers.has(company.ticker.toUpperCase()))
    .slice(0, 3)
  const anomalyResults = await Promise.allSettled(
    supportedCompanies.map((company) => getCompanyAnomalies(company.ticker, { limit: 5, signal })),
  )
  if (signal?.aborted) return null

  const signals = []
  let anomalyError = ''
  anomalyResults.forEach((result) => {
    if (result.status === 'fulfilled') {
      signals.push(...result.value.items)
    } else if (!anomalyError) {
      anomalyError = getApiErrorMessage(result.reason)
    }
  })
  signals.sort((a, b) => new Date(b.detected_at).getTime() - new Date(a.detected_at).getTime())
  next.anomalies = anomalyError && signals.length === 0
    ? { status: 'error', data: null, error: anomalyError }
    : { status: 'ready', data: signals.slice(0, 5), error: '', warning: anomalyError }
  return next
}

function OverviewPage() {
  const [sections, setSections] = useState(createInitialSections)
  const requestController = useRef(null)

  const executeLoad = useCallback(async (controller) => {
    try {
      const next = await loadOverviewSections(controller.signal)
      if (!controller.signal.aborted && next) setSections(next)
    } catch (error) {
      if (!controller.signal.aborted && !isRequestCanceled(error)) {
        setSections(createErrorSections(error))
      }
    } finally {
      if (requestController.current === controller) requestController.current = null
    }
  }, [])

  const refresh = useCallback(() => {
    requestController.current?.abort()
    const controller = new AbortController()
    requestController.current = controller
    setSections(createInitialSections())
    void executeLoad(controller)
  }, [executeLoad])

  useEffect(() => {
    const controller = new AbortController()
    requestController.current = controller
    void loadOverviewSections(controller.signal)
      .then((next) => {
        if (!controller.signal.aborted && next) setSections(next)
      })
      .catch((error) => {
        if (!controller.signal.aborted && !isRequestCanceled(error)) {
          setSections(createErrorSections(error))
        }
      })
      .finally(() => {
        if (requestController.current === controller) requestController.current = null
      })
    return () => {
      controller.abort()
      if (requestController.current === controller) requestController.current = null
    }
  }, [executeLoad])

  const totals = useMemo(() => ({
    companies: sections.companies.status === 'ready' ? sections.companies.total : null,
    watchlist: sections.watchlist.status === 'ready' ? sections.watchlist.total : null,
    reports: sections.reports.status === 'ready' ? sections.reports.total : null,
  }), [sections])

  return (
    <div className="overview-page">
      <PageIntro
        eyebrow={`${todayLabel} · INVESTMENT RESEARCH`}
        title="A clearer view of the evidence."
        description="Company fundamentals, emerging signals and the theses you are monitoring—together in one considered workspace."
        action={<LinkButton to="/research" variant="primary"><FileSearch size={16} /> Start research</LinkButton>}
      />

      <div className="overview-meta-row">
        <p className="overview-meta-row__note"><span className="status-dot" /> Workspace view · live records where available</p>
        <p className="overview-meta-row__date">Data shown reflects records available through the backend API.</p>
      </div>

      <section className="summary-strip" aria-label="Workspace totals">
        <SummaryItem label="Companies in scope" value={totals.companies} state={sections.companies.status} />
        <SummaryItem label="On your watchlist" value={totals.watchlist} state={sections.watchlist.status} />
        <SummaryItem label="Saved research reports" value={totals.reports} state={sections.reports.status} />
      </section>

      <div className="overview-grid overview-grid--main">
        <Panel
          eyebrow="RESEARCH LOG"
          title="Recent activity"
          className="overview-panel overview-panel--activity"
          action={<Link className="panel-link" to="/reports">All reports <ChevronRight size={15} /></Link>}
        >
          <SectionBody section={sections.reports} emptyTitle="No saved research yet" emptyDetail="Completed research appears here when it is saved as a report." onRetry={refresh} />
          {sections.reports.status === 'ready' && sections.reports.data.length > 0 && (
            <div className="activity-list">
              {sections.reports.data.slice(0, 4).map((report) => (
                <Link className="activity-row" to="/reports" key={report.id}>
                  <span className="activity-row__icon"><FileSearch size={17} /></span>
                  <span className="activity-row__body">
                    <span className="activity-row__title">{report.question}</span>
                    <span className="activity-row__meta">{report.ticker} <span>·</span> Saved research</span>
                  </span>
                  <span className="activity-row__date">{dateLabel(report.created_at)}</span>
                  <ArrowUpRight size={15} className="activity-row__arrow" aria-hidden="true" />
                </Link>
              ))}
            </div>
          )}
        </Panel>

        <Panel
          eyebrow="SIGNALS TO REVIEW"
          title="Recent anomalies"
          className="overview-panel overview-panel--signals"
          action={<Link className="panel-link" to="/anomalies">Signal register <ChevronRight size={15} /></Link>}
        >
          <SectionBody section={sections.anomalies} emptyTitle="No investigation signals available" emptyDetail="Signals appear here when the API has persisted results for a supported company." onRetry={refresh} />
          {sections.anomalies.status === 'ready' && sections.anomalies.data.length > 0 && (
            <div className="signal-list">
              {sections.anomalies.data.map((signal) => (
                <div className="signal-row" key={signal.id}>
                  <span className="signal-row__mark"><ShieldAlert size={16} /></span>
                  <div className="signal-row__body">
                    <div className="signal-row__topline"><span className="signal-row__ticker">{signal.ticker}</span><SeverityBadge value={signal.severity} /><span className="signal-row__date">{dateLabel(signal.detected_at)}</span></div>
                    <p className="signal-row__headline">{signal.headline}</p>
                    <p className="signal-row__type">{signal.signal_type.replaceAll('_', ' ')}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
          {sections.anomalies.warning && <p className="panel-warning">Some company signals could not be loaded. Results shown may be incomplete.</p>}
          <p className="panel-footnote">Investigation indicators only. They are not fraud probabilities or investment verdicts.</p>
        </Panel>
      </div>

      <div className="overview-grid overview-grid--lower">
        <Panel eyebrow="COMPANY UNIVERSE" title="Companies in focus" className="overview-panel">
          <SectionBody section={sections.companies} emptyTitle="No companies in the database" emptyDetail="Companies appear after the SEC data pipeline has processed them." onRetry={refresh} />
          {sections.companies.status === 'ready' && sections.companies.data.length > 0 && (
            <DataTable>
              <thead><tr><th scope="col">Company</th><th scope="col">Fiscal calendar</th><th scope="col">CIK</th><th scope="col" /></tr></thead>
              <tbody>
                {sections.companies.data.slice(0, 5).map((company) => (
                  <tr key={company.cik}>
                  <td><Link className="company-cell" to="/companies"><span className="ticker-mark">{company.ticker.slice(0, 1)}</span><span><strong>{company.name}</strong><small>{company.ticker}</small></span></Link></td>
                    <td className="muted-cell">{fiscalYearEndLabel(company.fiscal_year_end_month)}</td>
                    <td className="numeric-cell">{String(company.cik).padStart(10, '0')}</td>
                    <td><ChevronRight size={15} className="table-chevron" /></td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          )}
        </Panel>

        <Panel eyebrow="THESIS MONITOR" title="Recent thesis activity" className="overview-panel">
          <SectionBody section={sections.theses} emptyTitle="No investment theses yet" emptyDetail="Create a thesis to track measurable commitments over time." onRetry={refresh} />
          {sections.theses.status === 'ready' && sections.theses.data.length > 0 && (
            <div className="thesis-list">
              {sections.theses.data.slice(0, 4).map((thesis) => (
                <Link className="thesis-row" to="/thesis" key={thesis.id}>
                  <span className="thesis-row__ticker">{thesis.ticker}</span>
                  <span className="thesis-row__statement">{thesis.statement}</span>
                  <span className={`thesis-status thesis-status--${String(thesis.status || 'unassessed').toLowerCase().replaceAll(' ', '-')}`}>{thesis.status || 'Unassessed'}</span>
                </Link>
              ))}
            </div>
          )}
          <div className="panel-bottom-link"><LinkButton to="/thesis" variant="text">View investment theses</LinkButton></div>
        </Panel>
      </div>

      <Panel eyebrow="WATCHLIST" title="Companies on your watchlist" className="overview-panel watchlist-panel">
        <SectionBody section={sections.watchlist} emptyTitle="Your watchlist is clear" emptyDetail="Add a supported company when you are ready to keep it close." action={<LinkButton to="/watchlist" variant="text">Open watchlist</LinkButton>} onRetry={refresh} />
        {sections.watchlist.status === 'ready' && sections.watchlist.data.length > 0 && (
          <div className="watchlist-inline">
            {sections.watchlist.data.slice(0, 6).map((entry) => (
              <Link className="watchlist-chip" to="/watchlist" key={entry.cik}>
                <span className="watchlist-chip__ticker">{entry.ticker}</span>
                <span className="watchlist-chip__date"><Clock3 size={12} /> Added {dateLabel(entry.created_at)}</span>
                <ChevronRight size={15} />
              </Link>
            ))}
            <Link className="watchlist-add" to="/watchlist">Manage watchlist <ArrowUpRight size={14} /></Link>
          </div>
        )}
      </Panel>
    </div>
  )
}

function SummaryItem({ label, value, state }) {
  return (
    <div className="summary-item">
      <span className="summary-item__label">{label}</span>
      <span className={`summary-item__value${state === 'loading' ? ' summary-item__value--loading' : ''}`}>
        {state === 'loading' ? '—' : state === 'error' ? 'Unavailable' : value ?? '—'}
      </span>
    </div>
  )
}

function resultSection(result) {
  if (result.status === 'rejected') {
    return { status: 'error', data: null, error: getApiErrorMessage(result.reason) }
  }
  return {
    status: 'ready',
    data: result.value.items,
    total: result.value.total,
    error: '',
  }
}

export default OverviewPage
