import { describe, expect, it } from 'vitest'
import { getApiErrorMessage, isRequestCanceled } from './client.js'

describe('safe API error messages', () => {
  it.each([
    [400, 'The request could not be processed. Check the information and try again.'],
    [404, 'The requested records are not available.'],
    [409, 'This request conflicts with the current records. Refresh and try again.'],
    [422, 'Some request information was not accepted. Check it and try again.'],
    [500, 'The backend encountered a problem. Please try again later.'],
  ])('maps HTTP %i to a static message', (status, expected) => {
    const message = getApiErrorMessage({
      response: { status, data: { detail: 'postgres://private-dsn' } },
    })
    expect(message).toBe(expected)
    expect(message).not.toContain('postgres')
  })

  it('maps network, timeout, unexpected, and cancellation failures safely', () => {
    expect(getApiErrorMessage({ isAxiosError: true })).toMatch(/could not reach the backend/i)
    expect(getApiErrorMessage({ code: 'ECONNABORTED' })).toMatch(/took too long/i)
    expect(getApiErrorMessage(new Error('private database details')))
      .toBe('FinSight could not load this information. Please try again.')
    expect(getApiErrorMessage({ code: 'ERR_CANCELED' })).toBeNull()
    expect(isRequestCanceled({ code: 'ERR_CANCELED' })).toBe(true)
  })

  it('uses a safe message for malformed response errors', () => {
    expect(getApiErrorMessage({ name: 'InvalidApiResponseError', message: 'internal detail' }))
      .toBe('The backend returned an unexpected response. Please try again.')
  })
})
