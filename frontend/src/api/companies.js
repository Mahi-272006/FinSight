import { apiClient } from './client.js'
import { validatePageResponse, validateRecordResponse } from './validation.js'

export async function getCompany(ticker, { signal } = {}) {
  const { data } = await apiClient.get(`/companies/${encodeURIComponent(ticker)}`, { signal })
  return validateRecordResponse(data, 'company')
}

async function getCompanyPage(ticker, suffix, params, type, signal) {
  const { data } = await apiClient.get(
    `/companies/${encodeURIComponent(ticker)}${suffix}`,
    { params, signal },
  )
  return validatePageResponse(data, type)
}

export function getCompanyMetrics(ticker, { limit = 20, offset = 0, metric, signal } = {}) {
  const params = { limit, offset }
  if (metric) params.metric = metric
  return getCompanyPage(ticker, '/metrics', params, 'metrics', signal)
}

export function getCompanyMetricHistory(ticker, metric, { limit = 100, offset = 0, signal } = {}) {
  const path = `/metrics/${encodeURIComponent(metric)}/history`
  return getCompanyPage(ticker, path, { limit, offset }, 'metrics', signal)
}

export function getCompanyFacts(ticker, { limit = 20, offset = 0, metric, periodType, signal } = {}) {
  const params = { limit, offset }
  if (metric) params.metric = metric
  if (periodType) params.period_type = periodType
  return getCompanyPage(ticker, '/facts', params, 'facts', signal)
}
