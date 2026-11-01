import { apiClient } from './client.js'

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
const isDateString = (value) => isString(value) && !Number.isNaN(Date.parse(value))

const validators = {
  companies: (item) => isRecord(item)
    && isPositiveInteger(item.cik)
    && isString(item.ticker)
    && isString(item.name)
    && (item.fiscal_year_end_month === null
      || (Number.isInteger(item.fiscal_year_end_month)
        && item.fiscal_year_end_month >= 1 && item.fiscal_year_end_month <= 12))
    && isDateString(item.created_at)
    && isDateString(item.updated_at),
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

async function getPage(path, type, { limit, offset = 0, signal }) {
  const { data } = await apiClient.get(path, {
    params: { limit, offset },
    signal,
  })
  return validatePageResponse(data, type)
}

export function getCompanies({ limit = 100, offset = 0, signal } = {}) {
  return getPage('/companies', 'companies', { limit, offset, signal })
}

export function getWatchlist({ limit = 8, offset = 0, signal } = {}) {
  return getPage('/watchlist', 'watchlist', { limit, offset, signal })
}

export function getReports({ limit = 5, offset = 0, signal } = {}) {
  return getPage('/reports', 'reports', { limit, offset, signal })
}

export function getTheses({ limit = 5, offset = 0, signal } = {}) {
  return getPage('/theses', 'theses', { limit, offset, signal })
}

export function getCompanyAnomalies(ticker, { limit = 5, offset = 0, signal } = {}) {
  return getPage(`/anomalies/${encodeURIComponent(ticker)}`, 'anomalies', {
    limit,
    offset,
    signal,
  })
}
