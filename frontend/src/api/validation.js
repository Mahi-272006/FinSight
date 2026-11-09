export class InvalidApiResponseError extends Error {
  constructor() {
    super('The backend returned an unexpected response. Please try again.')
    this.name = 'InvalidApiResponseError'
  }
}

const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
const isString = (value) => typeof value === 'string' && value.trim().length > 0
const isPositiveInteger = (value) => Number.isInteger(value) && value > 0
const isNonNegativeInteger = (value) => Number.isInteger(value) && value >= 0
const isFiniteNumber = (value) => typeof value === 'number' && Number.isFinite(value)
const isDateString = (value) => isString(value) && !Number.isNaN(Date.parse(value))
const isNullableDateString = (value) => value === null || isDateString(value)
const isNullableFiniteNumber = (value) => value === null || isFiniteNumber(value)

const companyRecord = (item) => isRecord(item)
  && isPositiveInteger(item.cik)
  && isString(item.ticker)
  && isString(item.name)
  && (item.fiscal_year_end_month === null
    || (Number.isInteger(item.fiscal_year_end_month)
      && item.fiscal_year_end_month >= 1 && item.fiscal_year_end_month <= 12))
  && isDateString(item.created_at)
  && isDateString(item.updated_at)

const metricRecord = (item) => isRecord(item)
  && isPositiveInteger(item.id)
  && isPositiveInteger(item.cik)
  && isString(item.metric)
  && isNullableFiniteNumber(item.value)
  && isString(item.unit)
  && isPositiveInteger(item.fiscal_year)
  && isString(item.fiscal_period)
  && isString(item.period_type)
  && isString(item.formula)
  && isRecord(item.inputs)
  && Array.isArray(item.source_accessions)
  && item.source_accessions.every(isString)
  && isDateString(item.calculated_at)

const factRecord = (item) => isRecord(item)
  && isPositiveInteger(item.id)
  && isPositiveInteger(item.cik)
  && isString(item.metric)
  && isFiniteNumber(item.value)
  && isString(item.unit)
  && isString(item.period_type)
  && isPositiveInteger(item.fiscal_year)
  && isString(item.fiscal_period)
  && isNullableDateString(item.period_start)
  && isNullableDateString(item.period_end)
  && isString(item.source_concept)
  && isString(item.form)
  && isString(item.accession_number)
  && isNullableDateString(item.filed_at)
  && (item.source_url === null || isString(item.source_url))
  && typeof item.is_restated === 'boolean'
  && isNullableFiniteNumber(item.value_original)
  && isDateString(item.created_at)

// Thesis detail: conditions carry creation-time evidence snapshots and
// assessments carry append-only per-condition summaries with provenance.
const signalContextRecord = (item) => isRecord(item)
  && isString(item.signal_type)
  && isString(item.severity)
  && isString(item.period)
  && isString(item.headline)
  && isString(item.explanation)

const conditionEvaluationRecord = (item) => isRecord(item)
  && (item.condition_id === null || isPositiveInteger(item.condition_id))
  && isString(item.description)
  && isString(item.metric)
  && isString(item.comparator)
  && isFiniteNumber(item.threshold)
  && isString(item.verdict)
  && isNullableFiniteNumber(item.value)
  && (item.unit === null || isString(item.unit))
  && (item.tier === null || isString(item.tier))
  && (item.fiscal_year === null || Number.isInteger(item.fiscal_year))
  && (item.fiscal_period === null || isString(item.fiscal_period))
  && (item.formula === null || isString(item.formula))
  && Array.isArray(item.accessions)
  && item.accessions.every(isString)
  && isString(item.reason)

const creationEvidenceRecord = (item) => isRecord(item)
  && isString(item.metric)
  && isString(item.comparator)
  && isFiniteNumber(item.threshold)
  && isDateString(item.captured_at)
  && (item.value === null || isFiniteNumber(item.value))
  // A null value records "the metric did not exist yet" and carries no
  // unit/tier/period/formula/accessions keys at all.
  && (item.value === null || (
    isString(item.unit)
    && isString(item.tier)
    && isString(item.period)
    && (item.formula === null || isString(item.formula))
    && Array.isArray(item.accessions)
    && item.accessions.every(isString)
  ))

const assessmentRecord = (item) => isRecord(item)
  && isPositiveInteger(item.id)
  && isDateString(item.assessed_at)
  && isString(item.status)
  && (item.status_changed_from === null || isString(item.status_changed_from))
  && Array.isArray(item.summary)
  && item.summary.every(conditionEvaluationRecord)
  && isString(item.explanation)
  && isString(item.explanation_source)
  && Array.isArray(item.signals)
  && item.signals.every(signalContextRecord)

const thesisConditionRecord = (item) => isRecord(item)
  && isPositiveInteger(item.id)
  && isString(item.description)
  && isString(item.metric)
  && isString(item.comparator)
  && isFiniteNumber(item.threshold)
  && (item.fiscal_year === null || Number.isInteger(item.fiscal_year))
  && (item.fiscal_period === null || isString(item.fiscal_period))
  && creationEvidenceRecord(item.creation_evidence)

const thesisRecord = (item) => isRecord(item)
  && isPositiveInteger(item.id)
  && isString(item.ticker)
  && isString(item.statement)
  && isString(item.horizon)
  && isString(item.status)
  && isDateString(item.created_at)
  && Array.isArray(item.conditions)
  && item.conditions.every(thesisConditionRecord)
  && Array.isArray(item.assessments)
  && item.assessments.every(assessmentRecord)

const assessmentResultRecord = (item) => isRecord(item)
  && isPositiveInteger(item.assessment_id)
  && isPositiveInteger(item.thesis_id)
  && isString(item.status)
  && (item.status_changed_from === null || isString(item.status_changed_from))
  && Array.isArray(item.evaluations)
  && item.evaluations.every(conditionEvaluationRecord)
  && isString(item.explanation)
  && isString(item.explanation_source)
  && Array.isArray(item.signals_context)
  && item.signals_context.every(signalContextRecord)

// Research runs and stored reports carry the same 11-field ResearchResult
// body: the LLM answer, the deterministic validation report, the
// retrieved-tool audit and the recorded removals. The validation object may
// be null; when present its `ok` verdict is required while the five detail
// arrays stay optional (the contract guarantees the keys, but partial
// fixtures and future additions must not break rendering).
const stringArray = (value) => Array.isArray(value) && value.every(isString)

const researchValidationRecord = (item) => isRecord(item)
  && typeof item.ok === 'boolean'
  && (item.citations === undefined || stringArray(item.citations))
  && (item.unsupported_citations === undefined || stringArray(item.unsupported_citations))
  && (item.verified_numbers === undefined || stringArray(item.verified_numbers))
  && (item.unverified_numbers === undefined || stringArray(item.unverified_numbers))
  && (item.notes === undefined || stringArray(item.notes))

const toolCallRecord = (item) => isRecord(item)
  && isString(item.tool)
  && isRecord(item.args)
  && Array.isArray(item.chunk_ids)
  && item.chunk_ids.every(isString)

const researchResultBodyRecord = (item) => isRecord(item)
  && isString(item.question)
  && isString(item.ticker)
  // The run route 404s when the company is unknown and the report service
  // refuses to persist a result without one, so both 200s carry a record.
  && isRecord(item.company)
  && isPositiveInteger(item.company.cik)
  && isString(item.company.ticker)
  && isString(item.company.name)
  && isString(item.final_answer)
  && Array.isArray(item.citations)
  && item.citations.every(isString)
  && Array.isArray(item.limitations)
  && item.limitations.every(isString)
  && Array.isArray(item.trace)
  && item.trace.every(isString)
  && (item.validation === null || researchValidationRecord(item.validation))
  && isNonNegativeInteger(item.iterations)
  && isNonNegativeInteger(item.revises)
  && Array.isArray(item.tool_calls)
  && item.tool_calls.every(toolCallRecord)

// The /research response adds save metadata beside the body. Stored report
// detail does not: its `result` is a lossless snapshot of the body alone —
// thread_id sits at the response's top level, and save_requested/report_id
// are response-only — so the body record must never require them.
const researchResultRecord = (item) => researchResultBodyRecord(item)
  && isString(item.thread_id)
  && typeof item.save_requested === 'boolean'
  && (item.report_id === null || isPositiveInteger(item.report_id))

// Report list rows, and the summary half of a report-detail response.
const reportSummaryRecord = (item) => isRecord(item)
  && isPositiveInteger(item.id)
  && isPositiveInteger(item.cik)
  && isString(item.ticker)
  && isString(item.question)
  && isString(item.thread_id)
  && isDateString(item.created_at)

const reportDetailRecord = (item) => reportSummaryRecord(item)
  && researchResultBodyRecord(item.result)

const validators = {
  companies: companyRecord,
  company: companyRecord,
  watchlist: (item) => isRecord(item)
    && isPositiveInteger(item.cik)
    && isString(item.ticker)
    && isDateString(item.created_at),
  reports: reportSummaryRecord,
  reportDetail: reportDetailRecord,
  theses: (item) => isRecord(item)
    && isPositiveInteger(item.id)
    && isString(item.ticker)
    && isString(item.statement)
    && isString(item.horizon)
    && isString(item.status)
    && isDateString(item.created_at),
  thesis: thesisRecord,
  assessment: assessmentRecord,
  assessmentResult: assessmentResultRecord,
  researchResult: researchResultRecord,
  anomalies: (item) => isRecord(item)
    && isPositiveInteger(item.id)
    && isPositiveInteger(item.cik)
    && isString(item.ticker)
    && isString(item.signal_type)
    && isString(item.severity)
    && isPositiveInteger(item.fiscal_year)
    && isString(item.fiscal_period)
    && isString(item.period_type)
    && isString(item.headline)
    && isString(item.explanation)
    && isRecord(item.evidence)
    && Array.isArray(item.source_accessions)
    && item.source_accessions.every(isString)
    && isDateString(item.detected_at),
  metrics: metricRecord,
  facts: factRecord,
}

export function validatePageResponse(response, type) {
  const validateItem = validators[type]
  if (!validateItem || !isRecord(response) || !Array.isArray(response.items)
      || !isNonNegativeInteger(response.total)
      || !Number.isInteger(response.limit) || response.limit < 1 || response.limit > 100
      || !isNonNegativeInteger(response.offset)
      || (response.items.length > 0
        && response.total < response.offset + response.items.length)
      || response.items.length > response.limit
      || !response.items.every(validateItem)) {
    throw new InvalidApiResponseError()
  }
  return response
}

export function validateRecordResponse(response, type) {
  const validateItem = validators[type]
  if (!validateItem || !validateItem(response)) throw new InvalidApiResponseError()
  return response
}
