import { useRef, useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import { getAnomalySignals, recomputeAnomalySignals } from '../api/anomalies.js'
import { getCompanies } from '../api/overview.js'
import { getApiErrorMessage } from '../api/client.js'
import { useLoad } from '../hooks/useLoad.js'
import { Button, EmptyState, ErrorState, LoadingState, PageIntro, Pagination, Panel } from '../components/ui.jsx'
import './AnomaliesPage.css'

// Mirrors the backend SignalType enum; no endpoint exposes the catalog yet.
const signalTypes = [
  'earnings_cashflow_divergence',
  'receivables_outpacing_revenue',
  'margin_compression_streak',
  'growth_deceleration',
]
const pageSize = 20

function label(value) {
  return value.replaceAll('_', ' ')
}

function dateLabel(value) {
  if (!value) return 'Not recorded'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Not recorded' : new Intl.DateTimeFormat('en', {
    month: 'short', day: 'numeric', year: 'numeric',
  }).format(date)
}

function periodLabel(record) {
  return `${record.fiscal_period} ${record.fiscal_year}`
}

function SeverityBadge({ value }) {
  return <span className={`severity-badge severity-badge--${String(value).toLowerCase()}`}>{value}</span>
}

function recomputeErrorMessage(error) {
  const status = error?.response?.status
  const detail = error?.response?.data?.detail
  if (status === 404) return 'This company is no longer available. Choose another company and try again.'
  if (status === 422 && detail === 'Ticker is outside the supported company scope.') {
    return 'Recalculation is currently supported for MSFT, AAPL, and NVDA only.'
  }
  if (status === 422 && detail === 'Insufficient annual financial data to recompute signals.') {
    return 'There is not enough stored annual financial data to recalculate signals. Update the company’s financial metrics and try again.'
  }
  if (status === 422) return 'This company or request is not eligible for recalculation.'
  return getApiErrorMessage(error) ?? 'Recalculation did not complete. Please try again.'
}

function AnomaliesPage() {
  const [reload, setReload] = useState(0)
  const companiesState = useLoad((signal) => getCompanies({ limit: 100, signal }), [reload])
  const companies = companiesState.status === 'ready' ? companiesState.data.items : []
  const [selectedTicker, setSelectedTicker] = useState('')
  const activeTicker = companies.some((company) => company.ticker === selectedTicker)
    ? selectedTicker
    : companies[0]?.ticker ?? ''

  return (
    <div className="anomalies-page">
      <PageIntro
        eyebrow="INVESTIGATION SIGNALS"
        title="Anomalies"
        description="Deterministic signals derived from stored financial data. Every entry is a lead for closer review, not a conclusion of wrongdoing."
        action={companiesState.status === 'ready' && companies.length > 0 ? (
          <label className="anomaly-picker">
            <span>Selected company</span>
            <select aria-label="Select company" value={activeTicker} onChange={(event) => setSelectedTicker(event.target.value)}>
              {companies.map((company) => <option key={company.cik} value={company.ticker}>{company.ticker} · {company.name}</option>)}
            </select>
          </label>
        ) : null}
      />

      {companiesState.status === 'loading' && <Panel title="Company universe"><LoadingState label="Loading supported companies" /></Panel>}
      {companiesState.status === 'error' && <Panel title="Company universe"><ErrorState message={companiesState.error} onRetry={() => setReload((value) => value + 1)} /></Panel>}
      {companiesState.status === 'ready' && companies.length === 0 && (
        <Panel title="Company universe"><EmptyState title="No companies are available" detail="The API returned no company records." /></Panel>
      )}
      {activeTicker && <SignalRegister key={activeTicker} ticker={activeTicker} />}
    </div>
  )
}

function SignalRegister({ ticker }) {
  const [offset, setOffset] = useState(0)
  const [signalFilter, setSignalFilter] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [recomputing, setRecomputing] = useState(false)
  const [recomputeError, setRecomputeError] = useState('')
  const [recomputeNotice, setRecomputeNotice] = useState('')
  const recomputeInFlight = useRef(false)
  const registerState = useLoad((signal) => getAnomalySignals(ticker, {
    limit: pageSize, offset, signalType: signalFilter || undefined, signal,
  }), [ticker, offset, signalFilter, attempt])

  // Contract: an unfiltered request only 404s for an unknown ticker (a real
  // error), while a filtered request 404s when the filter matches no stored
  // rows (a legitimate empty result for a ticker the picker validated).
  const filteredEmpty = registerState.statusCode === 404 && signalFilter !== ''

  async function handleRecompute() {
    if (recomputeInFlight.current) return
    recomputeInFlight.current = true
    setRecomputing(true)
    setRecomputeError('')
    setRecomputeNotice('')
    try {
      const result = await recomputeAnomalySignals(ticker)
      setSignalFilter('')
      setOffset(0)
      setRecomputeNotice(result.total === 0
        ? `Recalculation completed for ${ticker}. No signals were found; previous signals for this company were cleared.`
        : `Recalculation completed for ${ticker}. ${result.total} signal${result.total === 1 ? ' is' : 's are'} now stored.`)
      setAttempt((value) => value + 1)
    } catch (error) {
      setRecomputeError(recomputeErrorMessage(error))
    } finally {
      recomputeInFlight.current = false
      setRecomputing(false)
    }
  }

  return (
    <Panel
      eyebrow="SIGNAL REGISTER"
      title={`Investigation signals for ${ticker}`}
      className="anomaly-panel"
      action={(
        <Button type="button" onClick={handleRecompute} disabled={recomputing}>
          {recomputing ? 'Recalculating…' : 'Recalculate signals'}
        </Button>
      )}
    >
      <p className="anomaly-panel__intro">Signals are produced by deterministic rules over stored metrics and filings. They mark periods that deserve a closer look; they are not findings of misstatement or fraud.</p>
      <p className="anomaly-recompute__notice">
        Recalculation uses stored annual financial metrics and replaces this company’s current signal set. A valid run can find no signals and clear previous entries. Nothing runs until you choose Recalculate signals.
      </p>
      {recomputeError && <p className="anomaly-recompute__error" role="alert">{recomputeError}</p>}
      {recomputeNotice && <p className="anomaly-recompute__success" role="status" aria-live="polite">{recomputeNotice}</p>}
      <div className="anomaly-filter">
        <label htmlFor="signal-filter">Signal type</label>
        <select
          id="signal-filter"
          value={signalFilter}
          onChange={(event) => { setSignalFilter(event.target.value); setOffset(0) }}
        >
          <option value="">All signal types</option>
          {signalTypes.map((name) => <option key={name} value={name}>{label(name)}</option>)}
        </select>
      </div>

      {registerState.status === 'loading' && <LoadingState label="Loading investigation signals" />}
      {registerState.status === 'error' && (filteredEmpty
        ? <EmptyState title="No signals of this type" detail={`No ${label(signalFilter)} signals are recorded for ${ticker}.`} />
        : <ErrorState message={registerState.error} onRetry={() => setAttempt((value) => value + 1)} />)}
      {registerState.status === 'ready' && registerState.data.items.length === 0 && (
        <EmptyState title="No investigation signals recorded" detail={`Deterministic rules have not flagged ${ticker} for review.`} />
      )}
      {registerState.status === 'ready' && registerState.data.items.length > 0 && (
        <>
          <div className="signal-list">
            {registerState.data.items.map((signal) => <SignalRow key={signal.id} signal={signal} />)}
          </div>
          <Pagination page={registerState.data} offset={offset} setOffset={setOffset} className="anomaly-pagination" />
        </>
      )}
    </Panel>
  )
}

function SignalRow({ signal }) {
  return (
    <article className="signal-row">
      <span className="signal-row__mark"><ShieldAlert size={16} /></span>
      <div className="signal-row__body">
        <div className="signal-row__topline">
          <span className="signal-row__ticker">{signal.ticker}</span>
          <SeverityBadge value={signal.severity} />
          <span className="signal-row__date">{dateLabel(signal.detected_at)}</span>
        </div>
        <p className="signal-row__headline">{signal.headline}</p>
        <p className="signal-row__type">{label(signal.signal_type)}</p>
        <p className="signal-row__period">{periodLabel(signal)} · {signal.period_type}</p>
        <p className="signal-row__explanation">{signal.explanation}</p>
        <div className="signal-row__meta">
          <details className="signal-evidence">
            <summary>Evidence</summary>
            <pre>{JSON.stringify(signal.evidence, null, 2)}</pre>
          </details>
          <span className="signal-accessions">
            Source accessions: {signal.source_accessions.length
              ? signal.source_accessions.join(', ')
              : 'No accession recorded'}
          </span>
        </div>
      </div>
    </article>
  )
}

export default AnomaliesPage
