import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchSignals } from '../api/signals'

// Module-level cache — survives component unmount/remount (page navigation).
const _cache = { signals: [], key: null, fetchedAt: 0 }
let _listeners = []

function _subscribe(fn) {
  _listeners.push(fn)
  return () => { _listeners = _listeners.filter(l => l !== fn) }
}

function _publish(signals) {
  _cache.signals = signals
  _listeners.forEach(fn => fn(signals))
}

// Treat cached data as fresh for 60 s with the same filters.
const STALE_MS = 60_000

export function useSignals() {
  const [signals, setSignals] = useState(_cache.signals)
  const [loading, setLoading]  = useState(false)
  const [error, setError]      = useState(null)
  const requestSeq             = useRef(0)

  // Stay in sync when another hook instance refreshes the cache
  useEffect(() => _subscribe(setSignals), [])

  const load = useCallback(async (filters = {}) => {
    const key = JSON.stringify(filters)
    const now = Date.now()

    // Return cached data immediately — no spinner on revisit
    if (_cache.signals.length > 0) {
      setSignals(_cache.signals)
      // Skip the network call entirely if the cache is still fresh
      if (key === _cache.key && now - _cache.fetchedAt < STALE_MS) return
    } else {
      // First-ever load: show spinner so the page isn't blank
      setLoading(true)
    }

    const seq = ++requestSeq.current
    setError(null)

    try {
      const data = await fetchSignals(filters)
      if (seq !== requestSeq.current) return  // stale response — newer fetch in flight
      _cache.key       = key
      _cache.fetchedAt = Date.now()
      _publish(data)
    } catch (e) {
      if (seq === requestSeq.current) setError(e.message)
    } finally {
      if (seq === requestSeq.current) setLoading(false)
    }
  }, [])

  // Expose invalidate so Recompute/Sync can force a fresh fetch
  const invalidate = useCallback(() => {
    _cache.key       = null
    _cache.fetchedAt = 0
  }, [])

  return { signals, loading, error, load, invalidate }
}
