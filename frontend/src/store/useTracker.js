import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchBets } from '../api/tracker'

// Module-level cache — survives component unmount/remount (page navigation).
// Shape: { bets: [], key: null, fetchedAt: 0 }
const _cache = { bets: [], key: null, fetchedAt: 0 }
let _listeners = []

function _subscribe(fn) {
  _listeners.push(fn)
  return () => { _listeners = _listeners.filter(l => l !== fn) }
}

function _publish(bets) {
  _cache.bets = bets
  _listeners.forEach(fn => fn(bets))
}

// Stale threshold — treat cached data as fresh for 60 s with the same filters.
const STALE_MS = 60_000

export function useTracker() {
  const [bets, setBets]       = useState(_cache.bets)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)
  const reqSeq                = useRef(0)

  // Stay in sync when another hook instance refreshes the cache
  useEffect(() => _subscribe(setBets), [])

  const loadBets = useCallback(async (filters = {}) => {
    const key = JSON.stringify(filters)
    const now = Date.now()

    // Return cached data immediately when available — no spinner on revisit
    if (_cache.bets.length > 0) {
      setBets(_cache.bets)
      // Skip the network call entirely if the cache is still fresh
      if (key === _cache.key && now - _cache.fetchedAt < STALE_MS) return
    } else {
      // First-ever load: show spinner so the page isn't blank
      setLoading(true)
    }

    const seq = ++reqSeq.current
    setError(null)

    try {
      const data = await fetchBets(filters)
      if (seq !== reqSeq.current) return   // stale response — a newer fetch is in flight
      _cache.key       = key
      _cache.fetchedAt = Date.now()
      _publish(data)
    } catch (e) {
      if (seq === reqSeq.current) setError(e.message)
    } finally {
      if (seq === reqSeq.current) setLoading(false)
    }
  }, [])

  // Expose an invalidate helper so Sync/Settle/CLV can force a fresh fetch
  const invalidate = useCallback(() => {
    _cache.key       = null
    _cache.fetchedAt = 0
  }, [])

  return { bets, loading, error, loadBets, invalidate }
}
