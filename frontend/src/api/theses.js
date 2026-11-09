import { apiClient } from './client.js'
import { validatePageResponse, validateRecordResponse } from './validation.js'

// Thesis register in stable creation order (limit/offset pagination).
export async function getTheses({ limit = 50, offset = 0, signal } = {}) {
  const { data } = await apiClient.get('/theses', { params: { limit, offset }, signal })
  return validatePageResponse(data, 'theses')
}

// One thesis: statement, conditions with creation snapshots, and the
// embedded assessment list used for the latest-assessment view.
export async function getThesis(thesisId, { signal } = {}) {
  const { data } = await apiClient.get(`/theses/${thesisId}`, { signal })
  return validateRecordResponse(data, 'thesis')
}

// Create a thesis plus its immutable condition snapshots. Never assesses:
// a new thesis starts as "Unassessed" until someone runs an assessment.
export async function createThesis(payload, { signal } = {}) {
  const { data } = await apiClient.post('/theses', payload, { signal })
  return validateRecordResponse(data, 'thesis')
}

// Run the deterministic assessment (append-only history row). `interpret`
// only controls whether the optional LLM writes explanatory prose -- it can
// never determine or override the status, which the backend derives from
// condition verdicts alone.
export async function assessThesis(thesisId, { interpret = false, signal } = {}) {
  const { data } = await apiClient.post(`/theses/${thesisId}/assess`, { interpret }, { signal })
  return validateRecordResponse(data, 'assessmentResult')
}

// Append-only assessment history, oldest first, paginated for the timeline.
export async function getAssessmentHistory(thesisId, { limit = 20, offset = 0, signal } = {}) {
  const { data } = await apiClient.get(`/theses/${thesisId}/assessments`, {
    params: { limit, offset },
    signal,
  })
  return validatePageResponse(data, 'assessment')
}
