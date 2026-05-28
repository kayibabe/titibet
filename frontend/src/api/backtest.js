import { apiFetch } from './client'

const BASE = '/api/backtest'

export async function runBacktest({ league_id, market, min_edge, date_from, date_to, engine, confidence_filter }) {
  const res = await apiFetch(`${BASE}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ league_id, market, min_edge, date_from, date_to, engine, confidence_filter }),
  })
  if (!res.ok) throw new Error(`Backtest run failed: ${res.status}`)
  return res.json()
}

export async function fetchBacktestResults() {
  const res = await apiFetch(`${BASE}/results`)
  if (!res.ok) throw new Error(`Results fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchBacktestSummary() {
  const res = await apiFetch(`${BASE}/summary`)
  if (!res.ok) throw new Error(`Summary fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchBankrollCurve() {
  const res = await apiFetch(`${BASE}/bankroll-curve`)
  if (!res.ok) throw new Error(`Bankroll curve fetch failed: ${res.status}`)
  return res.json()
}
