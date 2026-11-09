import { useState } from 'react'
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { ExternalLink } from 'lucide-react'
import {
  getCompany, getCompanyFacts, getCompanyMetricHistory, getCompanyMetrics,
} from '../api/companies.js'
import { getCompanies } from '../api/overview.js'
import { useLoad } from '../hooks/useLoad.js'
import {
  DataTable, EmptyState, ErrorState, LoadingState, PageIntro, Pagination, Panel,
} from '../components/ui.jsx'
import './CompaniesPage.css'

const metricNames = [
  'revenue_growth_yoy', 'operating_income_growth_yoy', 'net_income_growth_yoy',
  'operating_cash_flow_growth_yoy', 'gross_profit_margin', 'operating_income_margin',
  'net_income_margin', 'return_on_assets', 'debt_to_assets', 'free_cash_flow',
  'earnings_cashflow_divergence', 'current_ratio', 'accounts_receivable_growth_yoy',
]
const pageSize = 20

function fiscalYearEnd(month) {
  return month ? new Intl.DateTimeFormat('en', { month: 'long' }).format(new Date(2024, month - 1, 1)) : 'Not recorded'
}

function periodLabel(record) {
  return `${record.fiscal_period} ${record.fiscal_year}`
}

function metricLabel(value) {
  return value.replaceAll('_', ' ')
}

function displayValue(value, unit, metric) {
  if (value === null || value === undefined) return 'Not reported'

  if (unit === 'USD') {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0,
    }).format(value)
  }

  const formatted = new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 3,
  }).format(value)

  // Difference between two growth rates: percentage points
  if (metric === 'earnings_cashflow_divergence') {
    return `${new Intl.NumberFormat('en-US', {
      maximumFractionDigits: 1,
    }).format(value * 100)} pp`
  }

  // Ratios are stored as decimals, so convert them to percentages
  if (unit === 'ratio' && metric !== 'current_ratio') {
    return `${new Intl.NumberFormat('en-US', {
      maximumFractionDigits: 1,
    }).format(value * 100)}%`
  }

  return unit === 'ratio' ? `${formatted}×` : `${formatted} ${unit}`
}

function dateLabel(value) {
  if (!value) return 'Not recorded'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Not recorded' : new Intl.DateTimeFormat('en', {
    month: 'short', day: 'numeric', year: 'numeric',
  }).format(date)
}

function safeSourceUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && ['www.sec.gov', 'sec.gov'].includes(url.hostname)
      ? url.toString()
      : null
  } catch {
    return null
  }
}

function CompaniesPage() {
  const [reload, setReload] = useState(0)
  const companiesState = useLoad((signal) => getCompanies({ limit: 100, signal }), [reload])
  const companies = companiesState.status === 'ready' ? companiesState.data.items : []
  const [selectedTicker, setSelectedTicker] = useState('')
  const activeTicker = companies.some((company) => company.ticker === selectedTicker)
    ? selectedTicker
    : companies[0]?.ticker ?? ''

  return (
    <div className="companies-page">
      <PageIntro
        eyebrow="COMPANY RESEARCH"
        title="Companies"
        description="Review reported financial facts alongside deterministic calculated metrics and their source evidence."
        action={companiesState.status === 'ready' && companies.length > 0 ? (
          <label className="company-picker">
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
      {activeTicker && <CompanyResearch key={activeTicker} ticker={activeTicker} />}
    </div>
  )
}

function CompanyResearch({ ticker }) {
  const companyState = useLoad((signal) => getCompany(ticker, { signal }), [ticker])
  const [metricOffset, setMetricOffset] = useState(0)
  const [factOffset, setFactOffset] = useState(0)
  const [selectedMetric, setSelectedMetric] = useState('revenue_growth_yoy')
  const [metricFilter, setMetricFilter] = useState('')
  const metricPage = useLoad((signal) => getCompanyMetrics(ticker, {
    limit: pageSize, offset: metricOffset, metric: metricFilter || undefined, signal,
  }), [ticker, metricOffset, metricFilter])
  const factPage = useLoad((signal) => getCompanyFacts(ticker, {
    limit: pageSize, offset: factOffset, signal,
  }), [ticker, factOffset])
  const historyState = useLoad((signal) => getCompanyMetricHistory(ticker, selectedMetric, { signal }), [ticker, selectedMetric])

  return (
    <>
      <Panel eyebrow="COMPANY PROFILE" title={companyState.status === 'ready' ? companyState.data.name : ticker} className="company-profile">
        {companyState.status === 'loading' && <LoadingState label="Loading company profile" />}
        {companyState.status === 'error' && <ErrorState message={companyState.error} />}
        {companyState.status === 'ready' && (
          <dl className="company-profile__details">
            <div><dt>Ticker</dt><dd>{companyState.data.ticker}</dd></div>
            <div><dt>CIK</dt><dd className="tabular">{String(companyState.data.cik).padStart(10, '0')}</dd></div>
            <div><dt>Fiscal year end</dt><dd>{fiscalYearEnd(companyState.data.fiscal_year_end_month)}</dd></div>
            <div><dt>Company record updated</dt><dd>{dateLabel(companyState.data.updated_at)}</dd></div>
          </dl>
        )}
      </Panel>

      <Panel eyebrow="CALCULATED SERIES" title="Financial metrics" className="company-panel">
        <p className="company-panel__intro">Deterministic calculations from stored financial inputs. Formula and filing accession references are retained with each observation.</p>
        <div className="history-controls">
          <label htmlFor="metric-filter">Filter by metric</label>
          <select
            id="metric-filter"
            value={metricFilter}
            onChange={(event) => { setMetricFilter(event.target.value); setMetricOffset(0) }}
          >
            <option value="">All calculated metrics</option>
            {metricNames.map((name) => <option key={name} value={name}>{metricLabel(name)}</option>)}
          </select>
        </div>
        {metricPage.status === 'loading' && <LoadingState label="Loading calculated metrics" />}
        {metricPage.status === 'error' && (metricPage.statusCode === 404
          ? <EmptyState title="No observations for this metric" detail="The API has no stored observations for this company and metric." />
          : <ErrorState message={metricPage.error} />)}
        {metricPage.status === 'ready' && metricPage.data.items.length === 0 && <EmptyState title="No calculated metrics available" detail="No metric observations have been stored for this company." />}
        {metricPage.status === 'ready' && metricPage.data.items.length > 0 && <>
          <DataTable className="company-table">
            <thead><tr><th>Metric</th><th>Period</th><th>Value</th><th>Calculation</th><th>Source accessions</th></tr></thead>
            <tbody>{metricPage.data.items.map((item) => (
              <tr key={item.id}>
                <td className="metric-name">{metricLabel(item.metric)}</td>
                <td>{periodLabel(item)} <small className="record-subline">{item.period_type}</small></td>
                <td className="numeric-cell">{displayValue(item.value, item.unit, item.metric)}</td>
                <td><details className="evidence-details"><summary>Formula and inputs</summary><code>{item.formula}</code><pre>{JSON.stringify(item.inputs, null, 2)}</pre><small>Calculated {dateLabel(item.calculated_at)}</small></details></td>
                <td className="provenance-cell">{item.source_accessions.length ? item.source_accessions.join(', ') : 'No accession recorded'}</td>
              </tr>
            ))}</tbody>
          </DataTable>
          <Pagination page={metricPage.data} offset={metricOffset} setOffset={setMetricOffset} className="company-pagination" />
        </>}
      </Panel>

      <Panel eyebrow="HISTORICAL OBSERVATIONS" title="Metric history" className="company-panel">
        <div className="history-controls"><label htmlFor="metric-select">Calculated metric</label><select id="metric-select" value={selectedMetric} onChange={(event) => setSelectedMetric(event.target.value)}>{metricNames.map((name) => <option key={name} value={name}>{metricLabel(name)}</option>)}</select></div>
        {historyState.status === 'loading' && <LoadingState label="Loading metric history" />}
        {historyState.status === 'error' && (historyState.statusCode === 404
          ? <EmptyState title="No history for this metric" detail="The API has no stored observations for this company and metric." />
          : <ErrorState message={historyState.error} />)}
        {historyState.status === 'ready' && historyState.data.items.length === 0 && <EmptyState title="No history for this metric" detail="No stored observations are available." />}
        {historyState.status === 'ready' && historyState.data.items.length > 0 && <HistoryView page={historyState.data} metric={selectedMetric} />}
      </Panel>

      <Panel eyebrow="REPORTED EVIDENCE" title="Financial facts from filings" className="company-panel">
        <p className="company-panel__intro">As-filed facts retain their filing form, accession number and restatement context. These are reported values, distinct from calculated metrics.</p>
        {factPage.status === 'loading' && <LoadingState label="Loading reported facts" />}
        {factPage.status === 'error' && <ErrorState message={factPage.error} />}
        {factPage.status === 'ready' && factPage.data.items.length === 0 && <EmptyState title="No reported facts available" detail="No financial fact records have been stored for this company." />}
        {factPage.status === 'ready' && factPage.data.items.length > 0 && <>
          <DataTable className="company-table">
            <thead><tr><th>Reported metric</th><th>Fiscal period</th><th>Value</th><th>Filing</th><th>Accession / source</th><th>Restatement</th></tr></thead>
            <tbody>{factPage.data.items.map((fact) => {
              const sourceUrl = safeSourceUrl(fact.source_url)
              return <tr key={fact.id}>
                <td><span className="metric-name">{metricLabel(fact.metric)}</span><small className="record-subline">{fact.source_concept}</small></td>
                <td>{periodLabel(fact)}<small className="record-subline">{fact.period_type}{fact.period_start && fact.period_end ? ` · ${dateLabel(fact.period_start)}–${dateLabel(fact.period_end)}` : ''}</small></td>
                <td className="numeric-cell">{displayValue(fact.value, fact.unit, fact.metric)}</td>
                <td>{fact.form}<small className="record-subline">Filed {dateLabel(fact.filed_at)}</small></td>
                <td className="provenance-cell">{fact.accession_number}{sourceUrl && <a className="source-link" href={sourceUrl} target="_blank" rel="noreferrer">SEC filing <ExternalLink size={12} aria-hidden="true" /></a>}</td>
                <td>{fact.is_restated ? `Restated · original ${displayValue(fact.value_original, fact.unit, fact.metric)}` : 'As reported'}</td>
              </tr>
            })}</tbody>
          </DataTable>
          <Pagination page={factPage.data} offset={factOffset} setOffset={setFactOffset} className="company-pagination" />
        </>}
      </Panel>
    </>
  )
}

function HistoryView({ page, metric }) {
  const periodRank = { Q1: 1, Q2: 2, Q3: 3, Q4: 4, FY: 5 }

  const points = [...page.items].sort((a, b) =>
    a.fiscal_year - b.fiscal_year
    || (periodRank[a.fiscal_period] ?? 6) - (periodRank[b.fiscal_period] ?? 6)
  )

  const chartPoints = points.map((item) => ({
    ...item,
    fiscal_label: `${periodLabel(item)} · ${item.period_type}`,
  }))

  const unit = points[0]?.unit ?? ''
  return <>
    {points.length >= 2 && <div className="history-chart" role="img" aria-label={`${metricLabel(metric)} historical values by fiscal period`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartPoints} margin={{ top: 16, right: 20, left: 8, bottom: 4 }}>
          <CartesianGrid stroke="#ecece8" vertical={false} />
          <XAxis
  dataKey="fiscal_label"
  tick={{ fill: '#777', fontSize: 11 }}
  interval="preserveStartEnd"
/>
          <YAxis
  tick={{ fill: '#777', fontSize: 11 }}
  tickFormatter={(value) => {
    if (metric === 'earnings_cashflow_divergence') {
      return `${(value * 100).toFixed(0)} pp`
    }

    if (unit === 'ratio' && metric !== 'current_ratio') {
      return `${(value * 100).toFixed(0)}%`
    }

    return new Intl.NumberFormat('en-US', {
      notation: 'compact',
    }).format(value)
  }}
  width={64}
/>
          <Tooltip
  labelFormatter={(label, payload) => {
    const record = payload?.[0]?.payload
    return record
      ? `${periodLabel(record)} · ${record.period_type}`
      : label
  }}
  formatter={(value) => [
    displayValue(value, unit, metric),
    metricLabel(metric),
  ]}
/>
          <Line type="linear" dataKey="value" stroke="#596873" strokeWidth={2} dot={{ r: 3, fill: '#596873' }} connectNulls={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>}
    {page.total > page.items.length && (
      <p className="company-panel__intro">
        {`Showing the earliest ${page.items.length} of ${page.total} stored observations; newer periods exceed the API page limit and are not displayed.`}
      </p>
    )}
    {points.length < 2 && <p className="company-panel__intro">One observation is available; a trend line is shown when more historical periods are stored.</p>}
    <DataTable className="company-table company-table--history">
      <thead><tr><th>Fiscal period</th><th>Value</th><th>Unit</th><th>Source accessions</th></tr></thead>
      <tbody>{points.map((item) => <tr key={item.id}><td>{periodLabel(item)} · {item.period_type}</td><td className="numeric-cell">{displayValue(item.value, item.unit, metric)}</td><td>{item.unit}</td><td className="provenance-cell">{item.source_accessions.length ? item.source_accessions.join(', ') : 'No accession recorded'}</td></tr>)}</tbody>
    </DataTable>
  </>
}

export default CompaniesPage
