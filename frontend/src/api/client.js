const API_KEY = import.meta.env.VITE_API_KEY || ''
const TOKEN_KEY = 'titibet_token'

export async function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) }

  const jwt = localStorage.getItem(TOKEN_KEY)
  if (jwt) {
    headers['Authorization'] = `Bearer ${jwt}`
  }
  // When the backend has API_KEY set, requests still need this header unless JWT is accepted.
  // Sending both avoids 401 for logged-in users on older deployments / proxies.
  if (API_KEY) {
    headers['X-API-Key'] = API_KEY
  }

  const res = await fetch(url, { ...options, headers })
  return res
}
