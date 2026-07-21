import { apiFetch } from './client'

const BASE = '/api/analytics'

function buildParams(filters) {
  const params = new URLSearchParams()
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.market_type) params.set('market_type', filters.market_type)
  if (filters.league) params.set('league', filters.league)
  if (filters.result_status) params.set('result_status', filters.result_status)
  if (filters.source) params.set('source', filters.source)
  if (filters.scope)  params.set('scope',  filters.scope)
  return params.toString() ? `?${params}` : ''
}

/**
 * Primary analytics fetch — tries the efficient /full endpoint first (single DB query).
 * Falls back to the legacy fan-out pattern if /full returns 404 (backend not yet restarted).
 */
export async function fetchAnalytics(filters = {}) {
  const res = await apiFetch(`${BASE}/full${buildParams(filters)}`)
  if (res.ok) return res.json()

  // /full not available yet — fall back to parallel legacy calls
  if (res.status === 404) {
    const [summary, byMarket, byLeague, trend, streaks] = await Promise.all([
      apiFetch(`${BASE}/summary${buildParams(filters)}`).then(r => r.json()),
      apiFetch(`${BASE}/by-market${buildParams(filters)}`).then(r => r.json()),
      apiFetch(`${BASE}/by-league${buildParams(filters)}`).then(r => r.json()),
      apiFetch(`${BASE}/trend${buildParams(filters)}`).then(r => r.json()),
      apiFetch(`${BASE}/streaks`).then(r => r.json()),
    ])
    return {
      ...summary,
      by_market: byMarket,
      by_league: byLeague,
      daily_trend: trend,
      ...streaks,
    }
  }

  throw new Error(`Analytics fetch failed: ${res.status}`)
}

export async function fetchAnalyticsIntelligence() {
  const res = await apiFetch(`${BASE}/intelligence`)
  if (!res.ok) throw new Error(`Analytics intelligence fetch failed: ${res.status}`)
  return res.json()
}

// Legacy single-concern endpoints kept for backwards compatibility.
export async function fetchSummary(filters = {}) {
  const res = await apiFetch(`${BASE}/summary${buildParams(filters)}`)
  if (!res.ok) throw new Error(`Summary fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchByMarket(filters = {}) {
  const res = await apiFetch(`${BASE}/by-market${buildParams(filters)}`)
  if (!res.ok) throw new Error(`By-market fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchByLeague(filters = {}) {
  const res = await apiFetch(`${BASE}/by-league${buildParams(filters)}`)
  if (!res.ok) throw new Error(`By-league fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchTrend(filters = {}) {
  const res = await apiFetch(`${BASE}/trend${buildParams(filters)}`)
  if (!res.ok) throw new Error(`Trend fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchStreaks() {
  const res = await apiFetch(`${BASE}/streaks`)
  if (!res.ok) throw new Error(`Streaks fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchLossAnalysisSummary(lookbackDays = 30) {
  const res = await apiFetch(`/api/loss-analysis/summary?lookback_days=${lookbackDays}`)
  if (!res.ok) throw new Error(`Loss analysis fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchParameterStatus() {
  const res = await apiFetch(`${BASE}/parameter-status`)
  if (!res.ok) throw new Error(`Parameter status fetch failed: ${res.status}`)
  return res.json()
}

export async function triggerLossAnalysisPipeline(lookbackDays = 90) {
  const res = await apiFetch(`/api/loss-analysis/run?lookback_days=${lookbackDays}`, { method: 'POST' })
  if (!res.ok) throw new Error(`Loss analysis pipeline failed: ${res.status}`)
  return res.json()
}

export async function fetchStakingSimulation(filters = {}) {
  const res = await apiFetch(`${BASE}/staking-simulation${buildParams(filters)}`)
  if (!res.ok) throw new Error(`Staking simulation fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchProbabilityCalibration(filters = {}) {
  const res = await apiFetch(`${BASE}/probability-calibration${buildParams(filters)}`)
  if (!res.ok) throw new Error(`Probability calibration fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchModelIntelligence() {
  const res = await apiFetch(`${BASE}/model-intelligence`)
  if (!res.ok) throw new Error(`Model intelligence fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchAccaPerformance() {
  const res = await apiFetch(`${BASE}/acca-performance`)
  if (!res.ok) throw new Error(`ACCA performance fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchLeaderboard() {
  const res = await apiFetch('/api/leaderboard')
  if (!res.ok) throw new Error(`Leaderboard fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchSignalAccuracy(lookbackDays = 90) {
  const res = await apiFetch(`${BASE}/signal-accuracy?lookback_days=${lookbackDays}`)
  if (!res.ok) throw new Error(`Signal accuracy fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchOddsBandBreakdown(filters = {}) {
  const params = new URLSearchParams()
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to)   params.set('date_to',   filters.date_to)
  const qs = params.toString() ? `?${params}` : ''
  const res = await apiFetch(`${BASE}/odds-band-breakdown${qs}`)
  if (!res.ok) throw new Error(`Odds band breakdown fetch failed: ${res.status}`)
  return res.json()
}
