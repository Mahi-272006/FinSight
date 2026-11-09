import { useEffect, useRef, useState } from 'react'
import { runResearch } from '../api/research.js'
import { getCompanies } from '../api/overview.js'
import { getApiErrorMessage, isRequestCanceled } from '../api/client.js'
import { useLoad } from '../hooks/useLoad.js'
import { Button, EmptyState, LoadingState, PageIntro, Panel } from '../components/ui.jsx'
import { ResearchResultPanels, ValidationBadge } from '../components/ResearchResultPanels.jsx'
import './ResearchPage.css'

const QUESTION_MAX = 4000
const companiesLimit = 100

// The exact sanitized detail the backend sends (HTTP 503) when a run finished
// but the report insert failed. It is compared, never displayed: the raw
// backend detail must not reach the screen.
const REPORT_PERSISTENCE_DETAIL = 'Research completed, but report persistence failed.'

// Research-specific mapping on top of the shared handling: a 404 means the
// ticker is not an ingested company, a timeout means this client stopped
// waiting (the synchronous backend run has no deadline and may continue), and
// the known persistence 503 means the run itself succeeded. Everything else
// (500, network, malformed responses) keeps the shared wording, and no raw
// backend detail is ever shown.
function researchErrorMessage(error) {
  if (isRequestCanceled(error)) return null
  const status = error?.response?.status
  if (status === 404) {
    return 'Only companies ingested into FinSight can be researched. Choose a company from the list and try again.'
  }
  if (status === 503) {
    if (error?.response?.data?.detail === REPORT_PERSISTENCE_DETAIL) {
      return 'Research completed, but saving the report failed, so the result was not returned. Try running the question again.'
    }
    return getApiErrorMessage(error)
  }
  if (error?.code === 'ECONNABORTED' || error?.code === 'ETIMEDOUT') {
    return 'The client stopped waiting for this run, but the backend may still be processing it. Wait a moment, then try again.'
  }
  return getApiErrorMessage(error)
}

function saveNote(result) {
  if (!result.save_requested) return 'Not saved'
  if (result.report_id !== null) return `Saved as report #${result.report_id}`
  return 'Save requested, but no report was recorded'
}

function ResearchPage() {
  const [companiesAttempt, setCompaniesAttempt] = useState(0)
  const [ticker, setTicker] = useState('')
  const [question, setQuestion] = useState('')
  const [save, setSave] = useState(true)
  const [fieldErrors, setFieldErrors] = useState({})
  const [submitError, setSubmitError] = useState('')
  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  // Successful runs stay in memory only: no server-side history is fetched.
  const [runs, setRuns] = useState([])
  const [selectedKey, setSelectedKey] = useState(null)
  const controllerRef = useRef(null)
  const nextKeyRef = useRef(0)

  const companiesState = useLoad(
    (signal) => getCompanies({ limit: companiesLimit, signal }),
    [companiesAttempt],
  )
  const companies = companiesState.status === 'ready' ? companiesState.data.items : []

  // Abort the in-flight run only when the page itself unmounts. There is no
  // cancellation control: a timed-out or abandoned run may continue on the
  // backend, and this page never claims otherwise.
  useEffect(() => () => controllerRef.current?.abort(), [])

  useEffect(() => {
    if (!running) return undefined
    const startedAt = Date.now()
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - startedAt) / 1000)), 1000)
    return () => clearInterval(timer)
  }, [running])

  const selectedRun = runs.find((run) => run.key === selectedKey) ?? null

  async function submitRun() {
    const trimmed = question.trim()
    const errors = {}
    if (!ticker) errors.ticker = 'Choose a company.'
    if (!trimmed) errors.question = 'A question is required.'
    else if (trimmed.length > QUESTION_MAX) {
      errors.question = `The question must be ${QUESTION_MAX} characters or fewer.`
    }
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      setSubmitError('')
      return
    }

    setFieldErrors({})
    setSubmitError('')
    setElapsed(0)
    setRunning(true)
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      const result = await runResearch({ ticker, question: trimmed, save, signal: controller.signal })
      const key = nextKeyRef.current
      nextKeyRef.current += 1
      setRuns((prev) => [...prev, { key, result }])
      setSelectedKey(key)
    } catch (error) {
      if (isRequestCanceled(error)) return
      setSubmitError(
        researchErrorMessage(error)
        || 'FinSight could not run this research. Please try again.',
      )
    } finally {
      setRunning(false)
    }
  }

  function handleSubmit(event) {
    event.preventDefault()
    submitRun()
  }

  return (
    <div className="research-page">
      <PageIntro
        eyebrow="EVIDENCE-LED RESEARCH"
        title="AI Research"
        description="Ask an evidence-led question about an ingested company. The answer is generated by the LLM, checked deterministically against the retrieved filing evidence, and stripped of anything the evidence does not support."
      />

      <div className="research-layout">
        <div className="research-side">
          <Panel eyebrow="NEW QUESTION" title="Ask an evidence-led question" className="research-form-panel">
            <form className="research-form" onSubmit={handleSubmit} noValidate>
              <label className="research-field">
                <span>Company</span>
                {companiesState.status === 'loading' && (
                  <span className="research-field__hint">Loading companies…</span>
                )}
                {companiesState.status === 'error' && (
                  <span className="research-field__hint">
                    Companies are unavailable.{' '}
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setCompaniesAttempt((value) => value + 1)}
                    >
                      Try again
                    </button>
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
                {fieldErrors.ticker && (
                  <span className="research-field__error" role="alert">{fieldErrors.ticker}</span>
                )}
              </label>

              <div className="research-field-group">
                <label className="research-field">
                  <span>Question</span>
                  <textarea
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    rows={5}
                    placeholder="How did Microsoft turn revenue growth into margin expansion?"
                    aria-label="Question"
                  />
                </label>
                <span
                  className={`research-counter${question.length > QUESTION_MAX ? ' research-counter--over' : ''}`}
                >
                  {question.length}/{QUESTION_MAX}
                </span>
                {fieldErrors.question && (
                  <span className="research-field__error" role="alert">{fieldErrors.question}</span>
                )}
              </div>

              <label className="research-save">
                <input
                  type="checkbox"
                  checked={save}
                  onChange={(event) => setSave(event.target.checked)}
                />
                <span>Save this run as a report</span>
              </label>

              {submitError && (
                <div className="research-error" role="alert">
                  <p>{submitError}</p>
                  <Button type="button" variant="secondary" onClick={submitRun} disabled={running}>
                    Try again
                  </Button>
                </div>
              )}

              <div className="research-form__actions">
                <Button type="submit" disabled={running}>
                  {running ? 'Researching…' : 'Run research'}
                </Button>
              </div>
              <p className="research-form__hint">
                Nothing runs automatically. Research is synchronous and can take a minute or two;
                this page waits up to 5 minutes for the result.
              </p>
            </form>
          </Panel>

          <Panel eyebrow="THIS SESSION" title="Successful runs" className="research-runs-panel">
            {runs.length === 0 ? (
              <EmptyState
                title="No runs yet"
                detail="Completed runs from this visit appear here. The list is session-only and is not persisted."
              />
            ) : (
              <div className="research-runs">
                {runs.map((run) => (
                  <button
                    key={run.key}
                    type="button"
                    className={`research-run${run.key === selectedKey ? ' research-run--active' : ''}`}
                    aria-pressed={run.key === selectedKey}
                    onClick={() => setSelectedKey(run.key)}
                  >
                    <span className="research-run__top">
                      <span className="research-run__ticker">{run.result.ticker}</span>
                      <ValidationBadge validation={run.result.validation} />
                    </span>
                    <span className="research-run__question">{run.result.question}</span>
                  </button>
                ))}
              </div>
            )}
          </Panel>
        </div>

        <div className="research-detail">
          {running && (
            <Panel className="research-detail-panel">
              <LoadingState label={`Researching ${ticker} — ${elapsed}s elapsed`} />
              <p className="research-note">
                Runs are processed one at a time and can take a minute or two. Stay on this
                page; the answer panels appear when the run finishes.
              </p>
            </Panel>
          )}
          {!running && selectedRun && (
            <ResearchResultPanels result={selectedRun.result} saveLine={saveNote(selectedRun.result)} />
          )}
          {!running && !selectedRun && (
            <Panel className="research-detail-panel">
              <EmptyState
                title="No research run yet"
                detail="Choose an ingested company, ask a question, and run research. Nothing is submitted automatically."
              />
            </Panel>
          )}
        </div>
      </div>
    </div>
  )
}

export default ResearchPage
