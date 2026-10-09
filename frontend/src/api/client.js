// Local checkout API client: no JWT, API-key, or credential headers.
export async function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) }
  return fetch(url, { ...options, headers })
}
