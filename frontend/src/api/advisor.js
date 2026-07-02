import { apiFetch } from './client'

export async function fetchAdvisorInsights(date, { force = false } = {}) {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  if (force) params.set('force', 'true')
  const res = await apiFetch(`/api/advisor?${params}`)
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.detail || ''
    } catch (_) { /* non-JSON error body */ }
    if (res.status === 429) throw new Error(detail || 'Refresh is rate-limited — try again in a minute.')
    if (res.status === 403) throw new Error(detail || 'AI Advisory requires a Pro or Elite subscription.')
    throw new Error(detail || `Advisor request failed (${res.status}).`)
  }
  return res.json()
}
