import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import { assessThesis, createThesis, getAssessmentHistory, getThesis, getTheses } from '../api/theses.js'
import { getCompanies } from '../api/overview.js'
import { getCompanyMetrics } from '../api/companies.js'
import { getApiErrorMessage } from '../api/client.js'
import { useLoad } from '../hooks/useLoad.js'
import { Button, EmptyState, ErrorState, LoadingState, PageIntro, Pagination, Panel } from '../components/ui.jsx'
import './ThesisPage.css'

const listSize = 50
const historySize = 20
const fiscalPeriods = ['FY', 'Q1', 'Q2', 'Q3', 'Q4']

// Client-side mirror of the backend's parse_condition syntax, used only for
// live feedback while typing. The backend re-validates every submission and
// remains the authority (400/422 handling below).
const CONDITION_SYNTAX = /^[A-Za-z_][A-Za-z0-9_]*\s*(>=|<=|==|>|<|=|≥|≤)\s*-?\d+(?:\.\d+)?\s*$/

function statusBadge(status) {
  return <span className={`thesis-status thesis-status--${String(status).toLowerCase().replaceAll(' ', '-')}`}>{status}</span>
}

// Dates render in UTC so a stored instant reads the same everywhere.
function dateLabel(value, { withTime = false } = {}) {
  if (!value) return 'Not recorded'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Not recorded'
  const options = {
    timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric',
  }
  if (withTime) {
    options.hour = 'numeric'
    options.minute = '2-digit'
  }
  return new Intl.DateTimeFormat('en', options).format(date)
}

// Which layer wrote the explanation: deterministic verdict counts or the
// optional LLM (with provider/model provenance). Never the status itself.
function sourceBadge(source) {
  if (String(source).startsWith('llm:')) {
    return (
      <span className="source-badge source-badge--llm" title="Wording only -- the status badge is the deterministic result.">
        LLM interpretation · {String(source).slice(4)}
      </span>
    )
  }
  return (
    <span className="source-badge source-badge--deterministic" title="Generated from condition verdict counts.">
      Deterministic summary
    </span>
  )
}

function verdictLabel(verdict) {
  return { met: 'Met', not_met: 'Not met', incomplete: 'Incomplete' }[verdict] ?? verdict
}

function verdictBadge(verdict) {
  return <span className={`verdict verdict--${verdict}`}>{verdictLabel(verdict)}</span>
}

function fiscalLabel(year, period) {
  if (year === null || year === undefined) return null
  if (period && period !== 'FY') return `${period} FY${year}`
  return `FY${year}`
}

function pinLabel(condition) {
  return fiscalLabel(condition.fiscal_year, condition.fiscal_period) ?? 'latest available period'
}

function evaluationPeriod(evaluation) {
  return fiscalLabel(evaluation.fiscal_year, evaluation.fiscal_period)
}

function ThesisPage() {
  const [reload, setReload] = useState(0)
  const [listOffset, setListOffset] = useState(0)
  const [selectedId, setSelectedId] = useState(null)
  const [formOpen, setFormOpen] = useState(false)
  const [companiesAttempt, setCompaniesAttempt] = useState(0)

  const listState = useLoad(
    (signal) => getTheses({ limit: listSize, offset: listOffset, signal }),
    [listOffset, reload],
  )
  const items = listState.status === 'ready' ? listState.data.items : []
  const companiesState = useLoad(
    (signal) => getCompanies({ limit: 100, signal }),
    [companiesAttempt],
  )
  const companies = companiesState.status === 'ready' ? companiesState.data.items : []
  // An explicit selection always wins; otherwise the first register row.
  const activeId = selectedId ?? items[0]?.id ?? null

  return (
    <div className="theses-page">
      <PageIntro
        eyebrow="THESIS MONITORING"
        title="Investment Theses"
        description="Measurable commitments over stored financial data. Status is derived deterministically from condition verdicts; optional LLM wording is interpretation only, never the verdict."
        action={(
          <Button onClick={() => setFormOpen((open) => !open)}>
            {formOpen ? 'Close' : 'New thesis'}
          </Button>
        )}
      />

      {formOpen && (
        <ThesisForm
          companies={companies}
          companiesState={companiesState}
          onRetryCompanies={() => setCompaniesAttempt((value) => value + 1)}
          onCancel={() => setFormOpen(false)}
          onCreated={(created) => {
            setFormOpen(false)
            setSelectedId(created.id)
            setListOffset(0)
            setReload((value) => value + 1)
          }}
        />
      )}

      <div className="thesis-layout">
        <Panel eyebrow="REGISTER" title="All theses" className="thesis-register-panel">
          {listState.status === 'loading' && <LoadingState label="Loading theses" />}
          {listState.status === 'error' && (
            <ErrorState
              message={listState.error}
              onRetry={() => { setListOffset(0); setReload((value) => value + 1) }}
            />
          )}
          {listState.status === 'ready' && items.length === 0 && (
            <EmptyState
              title="No investment theses yet"
              detail="Create a thesis to track measurable commitments over time."
            />
          )}
          {listState.status === 'ready' && items.length > 0 && (
            <>
              <div className="thesis-register">
                {items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`thesis-pick${item.id === activeId ? ' thesis-pick--active' : ''}`}
                    aria-pressed={item.id === activeId}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <span className="thesis-pick__top">
                      <span className="thesis-pick__ticker">{item.ticker}</span>
                      {statusBadge(item.status)}
                    </span>
                    <span className="thesis-pick__statement">{item.statement}</span>
                    <span className="thesis-pick__meta">{item.horizon} · created {dateLabel(item.created_at)}</span>
                  </button>
                ))}
              </div>
              <Pagination page={listState.data} offset={listOffset} setOffset={setListOffset} className="thesis-pagination" />
            </>
          )}
        </Panel>

        {activeId !== null && <ThesisDetail key={activeId} thesisId={activeId} />}
        {activeId === null && listState.status === 'loading' && (
          <Panel className="thesis-detail-panel"><LoadingState label="Loading theses" /></Panel>
        )}
        {activeId === null && listState.status === 'error' && (
          <Panel className="thesis-detail-panel">
            <ErrorState message={listState.error} onRetry={() => setReload((value) => value + 1)} />
          </Panel>
        )}
        {activeId === null && listState.status === 'ready' && (
          <Panel className="thesis-detail-panel">
            <EmptyState
              title="No thesis selected"
              detail="Create a thesis or select one from the register to view its conditions and assessment history."
            />
          </Panel>
        )}
      </div>
    </div>
  )
}

function ThesisDetail({ thesisId }) {
  const [detailAttempt, setDetailAttempt] = useState(0)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyAttempt, setHistoryAttempt] = useState(0)
  const [interpret, setInterpret] = useState(false)
  const [assessing, setAssessing] = useState(false)
  const [assessError, setAssessError] = useState('')

  const detailState = useLoad((signal) => getThesis(thesisId, { signal }), [thesisId, detailAttempt])
  const historyState = useLoad(
    (signal) => getAssessmentHistory(thesisId, { limit: historySize, offset: historyOffset, signal }),
    [thesisId, historyOffset, historyAttempt],
  )

  async function handleAssess() {
    setAssessing(true)
    setAssessError('')
    try {
      await assessThesis(thesisId, { interpret })
      // Refresh both views: the authoritative status lives in the detail
      // response and the appended row appears at the end of history.
      setHistoryOffset(0)
      setDetailAttempt((value) => value + 1)
      setHistoryAttempt((value) => value + 1)
    } catch (error) {
      setAssessError(getApiErrorMessage(error) || 'FinSight could not run the assessment. Please try again.')
    } finally {
      setAssessing(false)
    }
  }

  if (detailState.status === 'loading') {
    return <Panel className="thesis-detail-panel"><LoadingState label="Loading thesis detail" /></Panel>
  }
  if (detailState.status === 'error') {
    return (
      <Panel className="thesis-detail-panel">
        <ErrorState
          message={detailState.error}
          onRetry={() => setDetailAttempt((value) => value + 1)}
        />
      </Panel>
    )
  }

  const thesis = detailState.data
  const latest = thesis.assessments.length > 0 ? thesis.assessments[thesis.assessments.length - 1] : null

  return (
    <div className="thesis-detail-column">
      <Panel
        eyebrow="THESIS DETAIL"
        title={`${thesis.ticker} · ${thesis.horizon}`}
        action={statusBadge(thesis.status)}
        className="thesis-detail-panel"
      >
        <p className="thesis-detail__statement">{thesis.statement}</p>
        <p className="thesis-detail__meta">Created {dateLabel(thesis.created_at)} · {thesis.conditions.length} condition(s)</p>

        <div className="latest-assessment">
          <p className="eyebrow">LATEST ASSESSMENT</p>
          {latest ? (
            <>
              <div className="latest-assessment__head">
                {statusBadge(latest.status)}
                <span className="latest-assessment__time">{dateLabel(latest.assessed_at, { withTime: true })}</span>
              </div>
              <p className="latest-assessment__caption">
                The status badge is the authoritative deterministic result, derived from condition verdicts alone.
              </p>
              <p className="latest-assessment__explanation">{latest.explanation}</p>
              {sourceBadge(latest.explanation_source)}
              {latest.status_changed_from && (
                <p className="latest-assessment__transition">
                  Transition recorded: {latest.status_changed_from} → {latest.status}
                </p>
              )}
            </>
          ) : (
            <p className="latest-assessment__none">
              Not yet assessed. Run an assessment to record the deterministic status.
            </p>
          )}
        </div>

        <div className="assess-controls">
          <Button onClick={handleAssess} disabled={assessing}>
            {assessing ? 'Assessing…' : 'Assess now'}
          </Button>
          <label className="assess-controls__option">
            <input
              type="checkbox"
              checked={interpret}
              onChange={(event) => setInterpret(event.target.checked)}
            />
            <span>Ask the LLM to interpret the verdicts (optional; wording only, never the status)</span>
          </label>
        </div>
        {assessError && <p className="thesis-field__error" role="alert">{assessError}</p>}
      </Panel>

      <Panel eyebrow="CONDITIONS" title="Measurable commitments">
        <p className="thesis-block__note">
          Each condition is immutable after creation. Its creation snapshot records what the metric
          looked like when the thesis was written, so later restatements show as drift instead of
          rewritten history.
        </p>
        <div className="condition-list">
          {thesis.conditions.map((condition) => (
            <article className="condition-card" key={condition.id}>
              <div className="condition-card__head">
                <span className="condition-card__metric">{condition.metric}</span>
                <span className="condition-card__compare">{condition.comparator} {condition.threshold}</span>
              </div>
              <p className="condition-card__description">{condition.description}</p>
              <p className="condition-card__pin">Period: {pinLabel(condition)}</p>
              <details className="condition-evidence">
                <summary>Evidence at creation</summary>
                <EvidenceSnapshot evidence={condition.creation_evidence} />
              </details>
            </article>
          ))}
        </div>
      </Panel>

      {latest && latest.signals.length > 0 && (
        <Panel eyebrow="CONTEXT ONLY" title="Investigation signals on the latest assessment">
          <p className="thesis-block__note">
            Attached as context only. Signals never determine or change thesis status.
          </p>
          <ul className="signal-context">
            {latest.signals.map((signal, index) => (
              <li key={`${signal.signal_type}-${index}`}>
                <span className={`severity-badge severity-badge--${String(signal.severity).toLowerCase()}`}>{signal.severity}</span>
                <span className="signal-context__type">{signal.signal_type}</span>
                <span className="signal-context__headline">{signal.headline}</span>
                <span className="signal-context__period">{signal.period}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel eyebrow="HISTORY" title="Assessment timeline" className="thesis-history-panel">
        <p className="thesis-block__note">
          Append-only. Each entry is a point-in-time snapshot; earlier rows are never rewritten.
        </p>
        {historyState.status === 'loading' && <LoadingState label="Loading assessment history" />}
        {historyState.status === 'error' && (
          <ErrorState
            message={historyState.error}
            onRetry={() => setHistoryAttempt((value) => value + 1)}
          />
        )}
        {historyState.status === 'ready' && historyState.data.items.length === 0 && (
          <EmptyState
            title="No assessments yet"
            detail="Run the first assessment to record a deterministic status for this thesis."
          />
        )}
        {historyState.status === 'ready' && historyState.data.items.length > 0 && (
          <>
            <ol className="assessment-timeline">
              {historyState.data.items.map((entry) => (
                <li className="assessment-entry" key={entry.id}>
                  <div className="assessment-entry__head">
                    {statusBadge(entry.status)}
                    <span className="assessment-entry__time">{dateLabel(entry.assessed_at, { withTime: true })}</span>
                    {entry.status_changed_from && (
                      <span className="assessment-entry__transition">
                        {entry.status_changed_from} → {entry.status}
                      </span>
                    )}
                  </div>
                  <p className="assessment-entry__explanation">{entry.explanation}</p>
                  {sourceBadge(entry.explanation_source)}
                  <div className="assessment-entry__evaluations">
                    {entry.summary.map((evaluation, evaluationIndex) => (
                      <div className="evaluation-row" key={`${entry.id}-${evaluationIndex}`}>
                        {verdictBadge(evaluation.verdict)}
                        <span className="evaluation-row__description">{evaluation.description}</span>
                        <span className="evaluation-row__numbers">
                          {evaluation.value !== null
                            ? `${evaluation.value}${evaluation.unit ? ` ${evaluation.unit}` : ''}`
                            : 'No value'}
                          {evaluation.tier ? ` · ${evaluation.tier}` : ''}
                          {evaluationPeriod(evaluation) ? ` · ${evaluationPeriod(evaluation)}` : ''}
                        </span>
                        {evaluation.formula && (
                          <span className="evaluation-row__formula">Formula: {evaluation.formula}</span>
                        )}
                        {evaluation.accessions.length > 0 && (
                          <span className="evaluation-row__accessions">
                            Accessions: {evaluation.accessions.join(', ')}
                          </span>
                        )}
                        <span className="evaluation-row__reason">{evaluation.reason}</span>
                      </div>
                    ))}
                  </div>
                  {entry.signals.length > 0 && (
                    <p className="assessment-entry__signals">
                      Context only: {entry.signals.length} investigation signal(s) attached — never verdicts.
                    </p>
                  )}
                </li>
              ))}
            </ol>
            <Pagination page={historyState.data} offset={historyOffset} setOffset={setHistoryOffset} className="thesis-pagination" />
          </>
        )}
      </Panel>
    </div>
  )
}

function EvidenceSnapshot({ evidence }) {
  if (evidence.value === null) {
    return (
      <p className="evidence-snapshot__missing">
        The metric had no rows when this thesis was written. Captured {dateLabel(evidence.captured_at)}.
      </p>
    )
  }
  return (
    <dl className="evidence-snapshot">
      <div><dt>Value</dt><dd>{evidence.value}{evidence.unit ? ` ${evidence.unit}` : ''}</dd></div>
      <div><dt>Tier</dt><dd>{evidence.tier}</dd></div>
      <div><dt>Period</dt><dd>{evidence.period}</dd></div>
      <div><dt>Formula</dt><dd>{evidence.formula ?? '—'}</dd></div>
      <div><dt>Accessions</dt><dd>{evidence.accessions.length ? evidence.accessions.join(', ') : '—'}</dd></div>
      <div><dt>Captured</dt><dd>{dateLabel(evidence.captured_at)}</dd></div>
    </dl>
  )
}

function ThesisForm({ companies, companiesState, onRetryCompanies, onCancel, onCreated }) {
  const [ticker, setTicker] = useState('')
  const [statement, setStatement] = useState('')
  const [horizon, setHorizon] = useState('')
  const [rows, setRows] = useState([''])
  const [fiscalYear, setFiscalYear] = useState('')
  const [fiscalPeriod, setFiscalPeriod] = useState('')
  const [fieldErrors, setFieldErrors] = useState({})
  const [submitError, setSubmitError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Optional metric suggestions for the condition builder, sourced from the
  // company metrics endpoint only while a company is selected.
  const metricsState = useLoad((signal) => (
    ticker
      ? getCompanyMetrics(ticker, { limit: 100, signal })
      : Promise.resolve({ items: [], total: 0, limit: 100, offset: 0 })
  ), [ticker])
  const metricNames = metricsState.status === 'ready'
    ? [...new Set(metricsState.data.items.map((metric) => metric.metric))].sort()
    : []

  async function handleSubmit(event) {
    event.preventDefault()
    const errors = {}
    const conditions = rows.map((row) => row.trim()).filter(Boolean)
    if (!ticker) errors.ticker = 'Choose a company.'
    if (!statement.trim()) errors.statement = 'A statement is required.'
    if (!horizon.trim()) errors.horizon = 'A horizon is required.'
    if (conditions.length === 0) {
      errors.conditions = 'Add at least one condition.'
    } else if (conditions.some((row) => !CONDITION_SYNTAX.test(row))) {
      errors.conditions = "Each condition must read 'metric OP threshold', e.g. revenue_growth_yoy >= 0.10."
    }
    const year = fiscalYear.trim() === '' ? null : Number(fiscalYear)
    if (fiscalYear.trim() !== '' && (!Number.isInteger(year) || year < 1)) {
      errors.fiscalYear = 'Fiscal year must be a whole number of 1 or more, or left empty.'
    }
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      setSubmitError('')
      return
    }

    setFieldErrors({})
    setSubmitError('')
    setSubmitting(true)
    try {
      const payload = {
        ticker,
        statement: statement.trim(),
        horizon: horizon.trim(),
        conditions,
      }
      if (year !== null) payload.fiscal_year = year
      if (fiscalPeriod) payload.fiscal_period = fiscalPeriod
      const created = await createThesis(payload)
      onCreated(created)
    } catch (error) {
      setSubmitError(
        getApiErrorMessage(error)
        || 'FinSight could not create this thesis. Please try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Panel eyebrow="NEW THESIS" title="Create an investment thesis" className="thesis-form-panel">
      <form className="thesis-form" onSubmit={handleSubmit} noValidate>
        <div className="thesis-form__grid">
          <label className="thesis-field">
            <span>Company</span>
            {companiesState.status === 'loading' && (
              <span className="thesis-field__hint">Loading companies…</span>
            )}
            {companiesState.status === 'error' && (
              <span className="thesis-field__hint">
                Companies are unavailable.{' '}
                <button type="button" className="text-button" onClick={onRetryCompanies}>Try again</button>
              </span>
            )}
            {companiesState.status === 'ready' && (
              <select value={ticker} onChange={(event) => setTicker(event.target.value)} aria-label="Company">
                <option value="">Select a company</option>
                {companies.map((company) => (
                  <option key={company.cik} value={company.ticker}>{company.ticker} · {company.name}</option>
                ))}
              </select>
            )}
            {fieldErrors.ticker && <span className="thesis-field__error" role="alert">{fieldErrors.ticker}</span>}
          </label>

          <label className="thesis-field">
            <span>Horizon</span>
            <input
              value={horizon}
              onChange={(event) => setHorizon(event.target.value)}
              placeholder="FY2026"
              maxLength={64}
              aria-label="Horizon"
            />
            {fieldErrors.horizon && <span className="thesis-field__error" role="alert">{fieldErrors.horizon}</span>}
          </label>
        </div>

        <label className="thesis-field">
          <span>Statement</span>
          <textarea
            value={statement}
            onChange={(event) => setStatement(event.target.value)}
            rows={3}
            maxLength={10000}
            placeholder="Microsoft sustains double-digit revenue growth through FY2026."
            aria-label="Statement"
          />
          {fieldErrors.statement && <span className="thesis-field__error" role="alert">{fieldErrors.statement}</span>}
        </label>

        <fieldset className="condition-builder">
          <legend>Conditions</legend>
          <p className="thesis-form__hint">
            Syntax: metric OP threshold — e.g. revenue_growth_yoy &gt;= 0.10, with OP one of
            {' '}&gt;= &lt;= &gt; &lt; ==. Suggestions come from the selected company&apos;s stored metrics.
          </p>
          {rows.map((row, index) => {
            const invalid = row.trim() !== '' && !CONDITION_SYNTAX.test(row)
            return (
              <div className="condition-row" key={index}>
                <input
                  list="thesis-metric-suggestions"
                  value={row}
                  placeholder="revenue_growth_yoy >= 0.10"
                  aria-label={`Condition ${index + 1}`}
                  aria-invalid={invalid || undefined}
                  aria-describedby={invalid ? `condition-error-${index}` : undefined}
                  onChange={(event) => setRows(rows.map((value, rowIndex) => (
                    rowIndex === index ? event.target.value : value
                  )))}
                />
                <button
                  type="button"
                  className="condition-row__remove"
                  aria-label={`Remove condition ${index + 1}`}
                  disabled={rows.length === 1}
                  onClick={() => setRows(rows.filter((_, rowIndex) => rowIndex !== index))}
                >
                  <X size={14} />
                </button>
                {invalid && (
                  <p className="condition-row__error" id={`condition-error-${index}`}>
                    Expected &apos;metric OP threshold&apos;, e.g. revenue_growth_yoy &gt;= 0.10.
                  </p>
                )}
              </div>
            )
          })}
          <button
            type="button"
            className="text-button condition-builder__add"
            onClick={() => setRows([...rows, ''])}
          >
            <Plus size={14} aria-hidden="true" /> Add condition
          </button>
          {fieldErrors.conditions && (
            <span className="thesis-field__error" role="alert">{fieldErrors.conditions}</span>
          )}
          <datalist id="thesis-metric-suggestions">
            {metricNames.map((name) => <option key={name} value={name} />)}
          </datalist>
        </fieldset>

        <div className="thesis-form__grid">
          <label className="thesis-field">
            <span>Fiscal year (optional)</span>
            <input
              type="number"
              min={1}
              step={1}
              value={fiscalYear}
              onChange={(event) => setFiscalYear(event.target.value)}
              placeholder="e.g. 2026"
            />
            {fieldErrors.fiscalYear && (
              <span className="thesis-field__error" role="alert">{fieldErrors.fiscalYear}</span>
            )}
          </label>

          <label className="thesis-field">
            <span>Fiscal period (optional)</span>
            <select
              value={fiscalPeriod}
              onChange={(event) => setFiscalPeriod(event.target.value)}
            >
              <option value="">Latest available</option>
              {fiscalPeriods.map((period) => <option key={period} value={period}>{period}</option>)}
            </select>
          </label>
        </div>

        {submitError && <p className="thesis-field__error" role="alert">{submitError}</p>}

        <div className="thesis-form__actions">
          <Button type="submit" disabled={submitting}>
            {submitting ? 'Creating…' : 'Create thesis'}
          </Button>
          <Button type="button" variant="secondary" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
        </div>
        <p className="thesis-form__hint">
          A new thesis starts as “Unassessed”. Nothing is assessed automatically — review the
          conditions, then run “Assess now”.
        </p>
      </form>
    </Panel>
  )
}

export default ThesisPage
