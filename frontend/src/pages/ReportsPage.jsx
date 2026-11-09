import { useState } from 'react'
import { getReports } from '../api/overview.js'
import { getReport } from '../api/reports.js'
import { useLoad } from '../hooks/useLoad.js'
import {
  EmptyState, ErrorState, LinkButton, LoadingState, PageIntro, Pagination, Panel,
} from '../components/ui.jsx'
import { ResearchResultPanels } from '../components/ResearchResultPanels.jsx'
import './ReportsPage.css'

const pageSize = 50

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

function ReportsPage() {
  const [listAttempt, setListAttempt] = useState(0)
  const [offset, setOffset] = useState(0)
  const [selectedId, setSelectedId] = useState(null)

  const listState = useLoad(
    (signal) => getReports({ limit: pageSize, offset, signal }),
    [offset, listAttempt],
  )
  const items = listState.status === 'ready' ? listState.data.items : []
  // An explicit selection always wins; otherwise the newest stored report
  // (the server pages by created_at DESC, id DESC, so row one is the newest).
  const activeId = selectedId ?? items[0]?.id ?? null

  return (
    <div className="reports-page">
      <PageIntro
        eyebrow="SAVED RESEARCH"
        title="Reports"
        description="Saved research runs, preserved exactly as generated: the answer, its deterministic evidence check, the cited accessions, the retrieved tool calls and the audit trace behind every stored report."
      />

      <div className="reports-layout">
        <Panel eyebrow="ALL REPORTS" title="Saved research reports" className="reports-list-panel">
          {listState.status === 'loading' && <LoadingState label="Loading saved reports" />}
          {listState.status === 'error' && (
            <ErrorState
              message={listState.error}
              onRetry={() => { setOffset(0); setListAttempt((value) => value + 1) }}
            />
          )}
          {listState.status === 'ready' && items.length === 0 && (
            <EmptyState
              title="No saved research yet"
              detail="Completed research appears here when it is saved as a report."
              action={<LinkButton to="/research">Start research</LinkButton>}
            />
          )}
          {listState.status === 'ready' && items.length > 0 && (
            <>
              <div className="reports-list">
                {items.map((report) => (
                  <button
                    key={report.id}
                    type="button"
                    className={`report-pick${report.id === activeId ? ' report-pick--active' : ''}`}
                    aria-pressed={report.id === activeId}
                    onClick={() => setSelectedId(report.id)}
                  >
                    <span className="report-pick__top">
                      <span className="report-pick__id">Report #{report.id}</span>
                      <span className="report-pick__ticker">{report.ticker}</span>
                    </span>
                    <span className="report-pick__question">{report.question}</span>
                    <span className="report-pick__meta">Saved {dateLabel(report.created_at)}</span>
                  </button>
                ))}
              </div>
              <Pagination
                page={listState.data}
                offset={offset}
                setOffset={setOffset}
                className="reports-pagination"
              />
            </>
          )}
        </Panel>

        {activeId !== null && <ReportDetail key={activeId} reportId={activeId} />}
        {activeId === null && listState.status === 'loading' && (
          <Panel className="reports-detail-panel"><LoadingState label="Loading saved reports" /></Panel>
        )}
        {activeId === null && listState.status === 'error' && (
          <Panel className="reports-detail-panel">
            <ErrorState message={listState.error} onRetry={() => setListAttempt((value) => value + 1)} />
          </Panel>
        )}
        {activeId === null && listState.status === 'ready' && (
          <Panel className="reports-detail-panel">
            <EmptyState
              title="No report selected"
              detail="Select a saved report from the list to read its answer and the evidence behind it."
            />
          </Panel>
        )}
      </div>
    </div>
  )
}

function ReportDetail({ reportId }) {
  const [detailAttempt, setDetailAttempt] = useState(0)
  const detailState = useLoad((signal) => getReport(reportId, { signal }), [reportId, detailAttempt])

  if (detailState.status === 'loading') {
    return <Panel className="reports-detail-panel"><LoadingState label="Loading report" /></Panel>
  }
  if (detailState.status === 'error') {
    // The id came from a real list row, so 404 means the report was removed
    // since the list was read. Static wording; the raw backend detail is
    // never rendered.
    if (detailState.statusCode === 404) {
      return (
        <Panel className="reports-detail-panel">
          <EmptyState
            title="Report not found"
            detail="This report is no longer available. It may have been removed; select another report from the list."
          />
        </Panel>
      )
    }
    return (
      <Panel className="reports-detail-panel">
        <ErrorState message={detailState.error} onRetry={() => setDetailAttempt((value) => value + 1)} />
      </Panel>
    )
  }

  const report = detailState.data

  return (
    <div className="reports-detail-column">
      <Panel
        eyebrow="REPORT DETAIL"
        title={`Report #${report.id} · ${report.ticker}`}
        className="reports-detail-panel"
        action={<span className="reports-saved">Saved as report #{report.id}</span>}
      >
        <p className="reports-detail__question">{report.question}</p>
        <p className="reports-detail__meta">
          Saved {dateLabel(report.created_at, { withTime: true })} · CIK {String(report.cik).padStart(10, '0')}
        </p>
      </Panel>
      <ResearchResultPanels result={report.result} saveLine={`Saved as report #${report.id}`} />
    </div>
  )
}

export default ReportsPage
