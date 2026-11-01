import axios from 'axios'

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

export const apiClient = axios.create({
  baseURL: configuredBaseUrl.replace(/\/$/, ''),
  timeout: 12000,
  headers: { Accept: 'application/json' },
})

export function getApiErrorMessage(error) {
  if (isRequestCanceled(error)) return null
  if (error?.name === 'InvalidApiResponseError') {
    return 'The backend returned an unexpected response. Please try again.'
  }
  if (error?.code === 'ECONNABORTED' || error?.code === 'ETIMEDOUT') {
    return 'The request took too long. Please try again.'
  }

  const status = error?.response?.status
  const messages = {
    400: 'The request could not be processed. Check the information and try again.',
    404: 'The requested records are not available.',
    409: 'This request conflicts with the current records. Refresh and try again.',
    422: 'Some request information was not accepted. Check it and try again.',
    500: 'The backend encountered a problem. Please try again later.',
  }
  if (messages[status]) return messages[status]
  if (!error?.response && (error?.isAxiosError || error?.code === 'ERR_NETWORK')) {
    return 'FinSight could not reach the backend. Check that the API is running and try again.'
  }
  return 'FinSight could not load this information. Please try again.'
}

export function isRequestCanceled(error) {
  return axios.isCancel(error) || error?.code === 'ERR_CANCELED' || error?.name === 'AbortError'
}
