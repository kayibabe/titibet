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

export async function deleteBet(id) {
  const res = await apiFetch(`${BASE}/bets/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`Delete bet failed: ${res.status}`)
  return res.json()
}

export async function deduplicateBets() {
  const res = await apiFetch(`${BASE}/bets/deduplicate`, { method: 'POST' })
  if (!res.ok) throw new Error(`Dedup failed: ${res.status}`)
  return res.json()
}

export async function normalizeStakes(stake = 50_000) {
  const res = await apiFetch(`${BASE}/bets/normalize-stakes?stake=${stake}`, { method: 'POST' })
  if (!res.ok) throw new Error(`Normalize stakes failed: ${res.status}`)
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

export async function fetchModelInsights() {
  const res = await apiFetch(`${BASE}/analytics/model-insights`)
  if (!res.ok) throw new Error(`Model insights failed: ${res.status}`)
  return res.json()
}

/**
 * Auto-track a signal as a system pick.  Called fire-and-forget from SignalsPage
 * whenever today's signals are loaded.  source_rule_key='system_auto' lets the
 * TrackerPage distinguish system picks from manual ones.
 */
const DEFAULT_FLAT_STAKE = 50_000

export async function autoTrackSignal(signal, { bankroll = DEFAULT_FLAT_STAKE } = {}) {
  const odds =
    signal.bayesian?.best_odd ||
    (signal.poisson?.prob > 0 ? parseFloat((1 / signal.poisson.prob).toFixed(2)) : null)
  if (!odds || odds <= 1.01) return null   // nothing valid to track

  const stakeAmt = DEFAULT_FLAT_STAKE

  const q = signal.dual_quality_score
  const grade = q == null ? null : q >= 0.08 ? 'A' : q >= 0.055 ? 'B' : q >= 0.035 ? 'C' : 'D'

  return trackPick({
    fixture_id:            signal.fixture_id,
    bookmaker:             signal.bayesian?.bookmaker || 'Best Available',
    event_date:            signal.event_date,
    match_name:            `${signal.home_team} vs ${signal.away_team}`,
    league:                signal.league,
    market_type:           signal.market,
    selection_name:        signal.market,
    odds,
    stake:                 stakeAmt,
    recommended_stake_pct: signal.dual_recommended_stake_pct,
    source_rule_key:       (signal.dual_confidence === 'High' && signal.dual_agreement === 'Both') ? 'system_dual' : 'system_auto',
    source_rule_label:     (signal.dual_confidence === 'High' && signal.dual_agreement === 'Both') ? 'Dual Signal (High+Both)' : 'System Auto-Pick',
    signal_grade:          grade,
    dual_confidence:       signal.dual_confidence,
    dual_agreement:        signal.dual_agreement,
  })
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
