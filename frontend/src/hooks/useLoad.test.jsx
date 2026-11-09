import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useLoad } from './useLoad.js'

const flush = () => new Promise((resolve) => { setTimeout(resolve, 0) })

describe('useLoad', () => {
  it('moves from loading to ready with the loaded data', async () => {
    const loader = vi.fn().mockResolvedValue({ items: [] })
    const { result } = renderHook(() => useLoad(loader, ['a']))

    expect(result.current.status).toBe('loading')
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.data).toEqual({ items: [] })
    expect(result.current.statusCode).toBeNull()
    expect(result.current.error).toBe('')
  })

  it('reports loading for a new key instead of the previous key data', async () => {
    const resolvers = []
    const loader = vi.fn(() => new Promise((resolve) => { resolvers.push(resolve) }))
    const { result, rerender } = renderHook(({ dep }) => useLoad(loader, [dep]), {
      initialProps: { dep: 1 },
    })

    resolvers[0]({ value: 1 })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.data).toEqual({ value: 1 })

    rerender({ dep: 2 })
    expect(result.current.status).toBe('loading')
    expect(result.current.data).toBeNull()
  })

  it('maps failures to a static message with the status code', async () => {
    const loader = vi.fn().mockRejectedValue({ response: { status: 500, data: { detail: 'database DSN secret' } } })
    const { result } = renderHook(() => useLoad(loader, ['b']))

    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.statusCode).toBe(500)
    expect(result.current.error).toBe('The backend encountered a problem. Please try again later.')
    expect(result.current.error).not.toContain('secret')
  })

  it('carries the status code so filtered-empty 404s can be distinguished', async () => {
    const loader = vi.fn().mockRejectedValue({ response: { status: 404 } })
    const { result } = renderHook(() => useLoad(loader, ['c']))

    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.statusCode).toBe(404)
    expect(result.current.error).toBe('The requested records are not available.')
  })

  it('aborts the in-flight request when the consumer unmounts', async () => {
    let capturedSignal
    const loader = vi.fn((signal) => {
      capturedSignal = signal
      return new Promise(() => {})
    })
    const { unmount } = renderHook(() => useLoad(loader, ['d']))

    unmount()
    expect(capturedSignal.aborted).toBe(true)
  })

  it('ignores canceled rejections without entering an error state', async () => {
    const loader = vi.fn().mockRejectedValue({ code: 'ERR_CANCELED' })
    const { result } = renderHook(() => useLoad(loader, ['e']))

    await flush()
    expect(result.current.status).toBe('loading')
    expect(result.current.error).toBe('')
  })

  it('aborts the previous request when the key changes', async () => {
    const signals = []
    const loader = vi.fn((signal) => {
      signals.push(signal)
      return new Promise(() => {})
    })
    const { rerender } = renderHook(({ dep }) => useLoad(loader, [dep]), {
      initialProps: { dep: 1 },
    })

    rerender({ dep: 2 })
    expect(signals).toHaveLength(2)
    expect(signals[0].aborted).toBe(true)
    expect(signals[1].aborted).toBe(false)
  })
})
