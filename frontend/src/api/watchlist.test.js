import { beforeEach, describe, expect, it, vi } from 'vitest'

// vi.mock must be declared before imports — Vitest hoists these to the top.
vi.mock('./client.js', () => ({
  apiClient: { post: vi.fn(), delete: vi.fn() },
}))

import { apiClient } from './client.js'
import { addWatchlistEntry, removeWatchlistEntry } from './watchlist.js'
import { InvalidApiResponseError } from './validation.js'

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const timestamp = '2026-11-01T12:00:00Z'

const entry = { cik: 320193, ticker: 'AAPL', created_at: timestamp }

// A helper that builds an Axios-style error (matches getApiErrorMessage expectations).
function axiosError(status, detail) {
  const err = new Error('Request failed')
  err.response = { status, data: { detail } }
  return err
}

// ---------------------------------------------------------------------------
// addWatchlistEntry
// ---------------------------------------------------------------------------

describe('addWatchlistEntry', () => {
  beforeEach(() => {
    apiClient.post.mockReset()
  })

  it('posts to /watchlist with the ticker body and validates the 201 response', async () => {
    apiClient.post.mockResolvedValue({ data: entry })

    const result = await addWatchlistEntry('AAPL')

    expect(result).toEqual(entry)
    expect(apiClient.post).toHaveBeenLastCalledWith(
      '/watchlist',
      { ticker: 'AAPL' },
      { signal: undefined },
    )
  })

  it('passes the abort signal through to apiClient', async () => {
    apiClient.post.mockResolvedValue({ data: entry })
    const signal = new AbortController().signal

    await addWatchlistEntry('AAPL', { signal })

    expect(apiClient.post).toHaveBeenLastCalledWith('/watchlist', { ticker: 'AAPL' }, { signal })
  })

  it('propagates a 409 error when the company is already on the watchlist', async () => {
    const err = axiosError(409, 'Company is already on the watchlist.')
    apiClient.post.mockRejectedValue(err)

    await expect(addWatchlistEntry('AAPL')).rejects.toThrow()
    // The raw error is re-thrown so the caller can inspect error.response.status.
    await expect(addWatchlistEntry('AAPL')).rejects.toMatchObject({ response: { status: 409 } })
  })

  it('propagates a 404 error when the company does not exist', async () => {
    apiClient.post.mockRejectedValue(axiosError(404, 'Company not found.'))

    await expect(addWatchlistEntry('ZZZZ')).rejects.toMatchObject({ response: { status: 404 } })
  })

  it('rejects with InvalidApiResponseError when the 201 body fails validation', async () => {
    // Missing required fields — validation must catch this.
    apiClient.post.mockResolvedValue({ data: { ticker: 'AAPL' } })

    await expect(addWatchlistEntry('AAPL')).rejects.toBeInstanceOf(InvalidApiResponseError)
  })

  it('rejects with InvalidApiResponseError when cik is not a number', async () => {
    apiClient.post.mockResolvedValue({ data: { ...entry, cik: 'not-a-number' } })

    await expect(addWatchlistEntry('AAPL')).rejects.toBeInstanceOf(InvalidApiResponseError)
  })
})

// ---------------------------------------------------------------------------
// removeWatchlistEntry
// ---------------------------------------------------------------------------

describe('removeWatchlistEntry', () => {
  beforeEach(() => {
    apiClient.delete.mockReset()
  })

  it('issues DELETE /watchlist/{ticker} and resolves to undefined on 204', async () => {
    apiClient.delete.mockResolvedValue({ data: '' }) // 204 → empty body

    const result = await removeWatchlistEntry('AAPL')

    expect(result).toBeUndefined()
    expect(apiClient.delete).toHaveBeenLastCalledWith('/watchlist/AAPL', { signal: undefined })
  })

  it('URL-encodes tickers before placing them in the delete path', async () => {
    apiClient.delete.mockResolvedValue({ data: '' })

    await removeWatchlistEntry('A/B')

    expect(apiClient.delete).toHaveBeenLastCalledWith('/watchlist/A%2FB', { signal: undefined })
  })

  it('passes the abort signal through to apiClient', async () => {
    apiClient.delete.mockResolvedValue({ data: '' })
    const signal = new AbortController().signal

    await removeWatchlistEntry('AAPL', { signal })

    expect(apiClient.delete).toHaveBeenLastCalledWith('/watchlist/AAPL', { signal })
  })

  it('propagates a 404 error when the entry is not on the watchlist', async () => {
    apiClient.delete.mockRejectedValue(axiosError(404, 'Watchlist entry not found.'))

    await expect(removeWatchlistEntry('AAPL')).rejects.toMatchObject({ response: { status: 404 } })
  })
})
