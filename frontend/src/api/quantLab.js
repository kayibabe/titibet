import { apiFetch } from './client'

export async function fetchEngineComparison({ market = '', minN = 30 } = {}) {
  const params = new URLSearchParams()
  if (market) params.set('market', market)
  params.set('min_n', String(minN))
  const qs = `?${params.toString()}`
  const res = await apiFetch(`/api/backtest/compare-engines${qs}`)
  if (!res.ok) throw new Error(`Engine comparison failed: ${res.status}`)
  return res.json()
}
