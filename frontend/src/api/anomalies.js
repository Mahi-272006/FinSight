import { apiClient } from './client.js'
import { validatePageResponse } from './validation.js'

/**
 * Read persisted investigation signals for one company.
 *
 * Detection never runs here: `GET /api/v1/anomalies/{ticker}` only reads
 * stored rows. A `signalType` the backend's enum accepts is passed through as
 * `signal_type`; filtered-empty responses arrive as 404 by contract.
 */
export async function getAnomalySignals(ticker, { limit = 20, offset = 0, signalType, signal } = {}) {
  const params = { limit, offset }
  if (signalType) params.signal_type = signalType
  const { data } = await apiClient.get(`/anomalies/${encodeURIComponent(ticker)}`, { params, signal })
  return validatePageResponse(data, 'anomalies')
}
