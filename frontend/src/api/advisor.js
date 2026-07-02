import { apiFetch } from './client'

async function throwApiError(res, fallback) {
  let detail = ''
  try {
    const body = await res.json()
    detail = body?.detail || ''
  } catch (_) { /* non-JSON error body */ }
  if (res.status === 429) throw new Error(detail || 'Rate-limited — try again in a minute.')
  if (res.status === 403) throw new Error(detail || 'AI Advisory requires a Pro or Elite subscription.')
  throw new Error(detail || `${fallback} (${res.status}).`)
}

export async function fetchAdvisorInsights(date, { force = false } = {}) {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  if (force) params.set('force', 'true')
  const res = await apiFetch(`/api/advisor?${params}`)
  if (!res.ok) await throwApiError(res, 'Advisor request failed')
  return res.json()
}

export async function trackAcca(date) {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  const res = await apiFetch(`/api/advisor/track-acca?${params}`, { method: 'POST' })
  if (!res.ok) await throwApiError(res, 'Tracking failed')
  return res.json()
}
