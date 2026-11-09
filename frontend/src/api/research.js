import { apiClient } from './client.js'
import { validateRecordResponse } from './validation.js'

// Synchronous agent runs are bounded by the LLM loop (30s per call, iteration
// and revise caps) but have no server-side deadline, so the client waits far
// longer than the shared 12s default before giving up.
export const RESEARCH_TIMEOUT_MS = 300000

// One synchronous research run. The backend generates the thread id and
// (unless `save` is false) persists the result as a report; neither is
// requested explicitly here because this stage has no thread continuation.
export async function runResearch({ ticker, question, save = true, signal } = {}) {
  const { data } = await apiClient.post(
    '/research',
    { ticker, question, save },
    { signal, timeout: RESEARCH_TIMEOUT_MS },
  )
  return validateRecordResponse(data, 'researchResult')
}
