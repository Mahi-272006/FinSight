import { useEffect, useRef, useState } from 'react'
import { getApiErrorMessage, isRequestCanceled } from '../api/client.js'

/**
 * Load API data with stale-request protection.
 *
 * - One AbortController per request; the previous request is aborted when the
 *   serialized request key changes and when the consumer unmounts.
 * - Results that arrive after abort (or as cancellations) are ignored.
 * - While a new key is pending, the hook reports `loading` instead of the
 *   previous key's data, so stale results are never rendered.
 * - Errors carry the HTTP `statusCode` so consumers can distinguish the
 *   backend's filtered-empty 404 from a real failure.
 *
 * The serialized request key is the explicit set of loader inputs.
 */
export function useLoad(loader, dependencies) {
  const [state, setState] = useState({ key: null, status: 'loading', data: null, error: '' })
  const loaderRef = useRef(loader)
  loaderRef.current = loader
  const key = JSON.stringify(dependencies)
  useEffect(() => {
    const controller = new AbortController()
    loaderRef.current(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ key, status: 'ready', data, error: '', statusCode: null })
      })
      .catch((error) => {
        if (!controller.signal.aborted && !isRequestCanceled(error)) {
          setState({ key, status: 'error', data: null, error: getApiErrorMessage(error), statusCode: error?.response?.status })
        }
      })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return state.key === key ? state : { status: 'loading', data: null, error: '', statusCode: null }
}

export default useLoad
