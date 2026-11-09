/**
 * watchlist.js — Watchlist mutation API functions.
 *
 * NOTE: getWatchlist (read) lives in overview.js and is imported from there.
 * This module only owns the mutating operations: add and remove.
 */

import { apiClient } from './client.js'
import { validateRecordResponse } from './validation.js'

/**
 * Add a company to the watchlist.
 *
 * POST /api/v1/watchlist
 * Body: { ticker }
 * Returns: WatchlistEntryResponse { cik, ticker, created_at }
 *
 * Throws an Axios error on failure:
 *   - 404 → "Company not found."
 *   - 409 → "Company is already on the watchlist."
 *
 * @param {string} ticker — ticker symbol (e.g. "AAPL")
 * @param {{ signal?: AbortSignal }} [opts]
 * @returns {Promise<{ cik: number, ticker: string, created_at: string }>}
 */
export async function addWatchlistEntry(ticker, { signal } = {}) {
  const { data } = await apiClient.post('/watchlist', { ticker }, { signal })
  return validateRecordResponse(data, 'watchlist')
}

/**
 * Remove a company from the watchlist.
 *
 * DELETE /api/v1/watchlist/{ticker}
 * Returns: 204 No Content (empty body).
 *
 * Throws an Axios error on failure:
 *   - 404 → "Watchlist entry not found."
 *
 * @param {string} ticker — ticker symbol (e.g. "AAPL")
 * @param {{ signal?: AbortSignal }} [opts]
 * @returns {Promise<void>}
 */
export async function removeWatchlistEntry(ticker, { signal } = {}) {
  await apiClient.delete(`/watchlist/${encodeURIComponent(ticker)}`, { signal })
}
