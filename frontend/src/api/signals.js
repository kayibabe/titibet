import { apiFetch } from './client'

const BASE = '/api/signals'

export async function fetchSignals({ date, confidence, agreement, market, min_quality, sort_by, best_per_fixture } = {}) {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  if (confidence) params.set('confidence', confidence)
  if (agreement) params.set('agreement', agreement)
  if (market) params.set('market', market)
  if (min_quality != null) params.set('min_quality', min_quality)
  if (sort_by) params.set('sort_by', sort_by)
  if (best_per_fixture === false) params.set('best_per_fixture', 'false')
  const res = await apiFetch(`${BASE}?${params}`)
  if (!res.ok) throw new Error(`Signals fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchFixtureSignals(fixtureId) {
  const res = await apiFetch(`${BASE}/${fixtureId}`)
  if (!res.ok) throw new Error(`Deep dive fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchMatchInfo(fixtureId) {
  const res = await apiFetch(`/api/signals/${fixtureId}/match-info`)
  if (!res.ok) throw new Error(`Match info fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchOddsMatrix(fixtureId) {
  const res = await apiFetch(`/api/signals/${fixtureId}/odds-matrix`)
  if (!res.ok) throw new Error(`Odds matrix fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchSignalExplanation(fixtureId, market) {
  const params = new URLSearchParams()
  if (market) params.set('market', market)
  const res = await apiFetch(`/api/signals/${fixtureId}/explain?${params}`)
  if (!res.ok) throw new Error(`Explanation fetch failed: ${res.status}`)
  return res.json()
}


export async function computeSignals(date) {
  const res = await apiFetch(`${BASE}/compute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ date }),
  })
  if (!res.ok) throw new Error(`Compute failed: ${res.status}`)
  return res.json()
}
