import { apiFetch } from './client'

const BASE = '/api/accumulators'

export async function fetchAccumulators(dateStr) {
  const url = dateStr ? `${BASE}?date=${dateStr}` : BASE
  const res = await apiFetch(url)
  if (!res.ok) throw new Error(`Accumulators fetch failed: ${res.status}`)
  return res.json()
}
