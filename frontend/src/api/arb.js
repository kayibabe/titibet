import { apiFetch } from './client'

export async function fetchArbOpportunities(date) {
  const params = date ? `?date=${date}` : ''
  const res = await apiFetch(`/api/arb/opportunities${params}`)
  if (!res.ok) throw new Error(`Arb fetch failed: ${res.status}`)
  return res.json()
}
