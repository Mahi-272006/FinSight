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

const validators = {
  companies: companyRecord,
  company: companyRecord,
  watchlist: (item) => isRecord(item)
    && isPositiveInteger(item.cik)
    && isString(item.ticker)
    && isDateString(item.created_at),
  reports: (item) => isRecord(item)
    && isPositiveInteger(item.id)
    && isPositiveInteger(item.cik)
    && isString(item.ticker)
    && isString(item.question)
    && isString(item.thread_id)
    && isDateString(item.created_at),
  theses: (item) => isRecord(item)
    && isPositiveInteger(item.id)
    && isString(item.ticker)
    && isString(item.statement)
    && isString(item.horizon)
    && isString(item.status)
    && isDateString(item.created_at),
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
