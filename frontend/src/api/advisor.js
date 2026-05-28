import { apiFetch } from './client'

export async function fetchAdvisorInsights(date) {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  const res = await apiFetch(`/api/advisor?${params}`)
  if (!res.ok) throw new Error(`Advisor fetch failed: ${res.status}`)
  return res.json()
}
