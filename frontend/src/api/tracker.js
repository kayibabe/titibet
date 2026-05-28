import { apiFetch } from './client'

const BASE = '/api/tracker'

export async function syncData(run_date, { force = false } = {}) {
  const params = new URLSearchParams()
  if (run_date) params.set('run_date', run_date)
  if (force) params.set('force', 'true')
  const qs = params.toString() ? `?${params}` : ''
  const res = await apiFetch(`${BASE}/sync${qs}`, { method: 'POST' })
  if (!res.ok) throw new Error(`Sync failed: ${res.status}`)
  return res.json()
}

export async function trackPick(payload) {
  const res = await apiFetch(`${BASE}/track-pick`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    const detail = Array.isArray(err.detail)
      ? err.detail.map(d => d.msg || JSON.stringify(d)).join('; ')
      : err.detail
    throw new Error(detail || `Track pick failed: ${res.status}`)
  }
  return res.json()
}

export async function fetchBets({ date_from, date_to, market_type, result_status } = {}) {
  const params = new URLSearchParams()
  if (date_from) params.set('date_from', date_from)
  if (date_to) params.set('date_to', date_to)
  if (market_type) params.set('market_type', market_type)
  if (result_status) params.set('result_status', result_status)
  const res = await apiFetch(`${BASE}/bets?${params}`)
  if (!res.ok) throw new Error(`Bets fetch failed: ${res.status}`)
  return res.json()
}

export async function updateBet(id, payload) {
  const res = await apiFetch(`${BASE}/bets/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`Update bet failed: ${res.status}`)
  return res.json()
}

export async function settleResults(run_date) {
  const params = run_date ? `?run_date=${run_date}` : ''
  const res = await apiFetch(`${BASE}/settle-results${params}`, { method: 'POST' })
  if (!res.ok) throw new Error(`Settle failed: ${res.status}`)
  return res.json()
}

export async function computeCLV({ force = false } = {}) {
  const res = await apiFetch(`${BASE}/compute-clv?force=${force}`, { method: 'POST' })
  if (!res.ok) throw new Error(`CLV compute failed: ${res.status}`)
  return res.json()
}

export async function fetchAccumulators() {
  const res = await apiFetch(`${BASE}/accumulators`)
  if (!res.ok) throw new Error(`Accumulators fetch failed: ${res.status}`)
  return res.json()
}

export async function createAccumulator(payload) {
  const res = await apiFetch(`${BASE}/accumulators`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`Create accumulator failed: ${res.status}`)
  return res.json()
}

export async function fetchTrackerAnalytics() {
  const res = await apiFetch(`${BASE}/analytics`)
  if (!res.ok) throw new Error(`Tracker analytics failed: ${res.status}`)
  return res.json()
}

export async function fetchRuns() {
  const res = await apiFetch(`${BASE}/runs`)
  if (!res.ok) throw new Error(`Runs fetch failed: ${res.status}`)
  return res.json()
}

export async function generateAccumulators({ date, min_odds = 35, max_odds = 60, top_n = 3 } = {}) {
  const res = await apiFetch(`${BASE}/accumulators/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ date, min_odds, max_odds, top_n }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    const detail = err?.detail || err?.message
    throw new Error(detail ? `${detail}` : `Generate accumulators failed: ${res.status}`)
  }
  return res.json()
}

export async function confirmAccumulator({ legs, stake, name, ranked_bucket_key, allow_over_100x = false }) {
  const res = await apiFetch(`${BASE}/accumulators/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ legs, stake, name, ranked_bucket_key, allow_over_100x }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Confirm accumulator failed: ${res.status}`)
  }
  return res.json()
}

export async function deleteAccumulator(id) {
  const res = await apiFetch(`${BASE}/accumulators/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Delete failed: ${res.status}`)
  }
  return res.json()
}

export async function deduplicateAccumulators() {
  const res = await apiFetch(`${BASE}/accumulators/deduplicate`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Deduplicate failed: ${res.status}`)
  }
  return res.json()   // { removed: number }
}

export async function fetchAccumulatorAnalytics() {
  const res = await apiFetch(`${BASE}/analytics/accumulators`)
  if (!res.ok) throw new Error(`Accumulator analytics failed: ${res.status}`)
  return res.json()
}

export async function confirmRecommendedTicket(payload) {
  const res = await apiFetch(`${BASE}/recommended-tickets/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || err.message || `Confirm recommended ticket failed: ${res.status}`)
  }
  return res.json()
}

export async function fetchModelInsights() {
  const res = await apiFetch(`${BASE}/analytics/model-insights`)
  if (!res.ok) throw new Error(`Model insights failed: ${res.status}`)
  return res.json()
}

/** Bulk-import historical bets from parsed CSV rows. */
export async function bulkImportBets(rows) {
  const res = await apiFetch(`${BASE}/bets/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rows),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Import failed: ${res.status}`)
  }
  return res.json()   // { imported, skipped, errors }
}
