import { apiClient } from './client.js'
import { validatePageResponse, validateRecordResponse } from './validation.js'

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

/**
 * Recalculate and replace the persisted investigation signals for a company.
 * The operation uses stored annual metrics and is only run after an explicit
 * user action. A valid run may return an empty signal set.
 */
export async function recomputeAnomalySignals(ticker, { signal } = {}) {
  const { data } = await apiClient.post('/anomalies/recompute', { ticker }, { signal })
  return validateRecordResponse(data, 'anomalyRecompute')
}
