import { apiClient } from './client.js'
import { validateRecordResponse } from './validation.js'

// One persisted report: the list-summary fields plus the stored
// ResearchResult body under `result` (the response-only save metadata and
// the top-level thread id are validated where they actually live). The
// paginated list itself is shared with the Overview through overview.js.
export async function getReport(reportId, { signal } = {}) {
  const { data } = await apiClient.get(`/reports/${reportId}`, { signal })
  return validateRecordResponse(data, 'reportDetail')
}
