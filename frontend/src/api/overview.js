import { apiClient } from './client.js'
import { validatePageResponse } from './validation.js'

export { InvalidApiResponseError, validatePageResponse } from './validation.js'

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
