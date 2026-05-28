/**
 * useAccaDraft — lightweight cross-page accumulator draft.
 *
 * Signals can be added from the Signals page without navigating away.
 * When the user opens the Tracker page the drafted legs are already loaded
 * into the AccumulatorBuilder.
 *
 * Storage: module-level singleton + localStorage. The draft persists across
 * page refreshes for the same browser session. Legs are keyed by fixture_id
 * so stale legs from a prior date are harmless (they simply won't match any
 * live signal) and the user can remove them manually.
 */
import { useState, useEffect } from 'react'

const MAX_LEGS = 8
const LS_KEY   = 'titibet_acca_draft'

function _load() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function _persist(legs) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(legs)) } catch { /* quota */ }
}

let _legs = _load()
const _listeners = new Set()

function _notify() {
  _listeners.forEach(fn => fn([..._legs]))
}

export function addAccaDraftLeg(signal) {
  // Deduplicate by fixture_id — one leg per game
  if (_legs.some(l => l.fixture_id === signal.fixture_id)) return
  if (_legs.length >= MAX_LEGS) return
  _legs = [
    ..._legs,
    {
      fixture_id:   signal.fixture_id,
      home_team:    signal.home_team,
      away_team:    signal.away_team,
      market:       signal.market,
      odds:         signal.bayesian?.best_odd ?? null,
      bookmaker:    signal.bayesian?.bookmaker ?? null,
      probability:  Math.max(signal.bayesian?.prob ?? 0, signal.poisson?.prob ?? 0) || null,
      confidence:   signal.dual_confidence,
      agreement:    signal.dual_agreement,
      kickoff_at:   signal.kickoff_at,
      league:       signal.league,
      country:      signal.country,
    },
  ]
  _persist(_legs)
  _notify()
}

export function removeAccaDraftLeg(fixtureId) {
  _legs = _legs.filter(l => l.fixture_id !== fixtureId)
  _persist(_legs)
  _notify()
}

export function clearAccaDraft() {
  _legs = []
  _persist(_legs)
  _notify()
}

/** React hook — returns [legs, add, remove, clear] */
export function useAccaDraft() {
  const [legs, setLegs] = useState([..._legs])

  useEffect(() => {
    _listeners.add(setLegs)
    return () => _listeners.delete(setLegs)
  }, [])

  return {
    legs,
    addLeg:   addAccaDraftLeg,
    removeLeg: removeAccaDraftLeg,
    clearDraft: clearAccaDraft,
    isFull:   legs.length >= MAX_LEGS,
    hasLeg:   (fixtureId) => legs.some(l => l.fixture_id === fixtureId),
  }
}
