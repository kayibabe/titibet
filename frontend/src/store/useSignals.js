import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchSignals } from '../api/signals'

// Module-level cache — survives component unmount/remount (page navigation).
const _cache = { signals: [], hiddenHighConfidenceCount: 0, key: null, fetchedAt: 0 }
let _listeners = []

function _subscribe(fn) {
  _listeners.push(fn)
  return () => { _listeners = _listeners.filter(l => l !== fn) }
}

function _publish(payload) {
  // Support both the new {signals, hidden_high_confidence_count} shape and the old bare array
  const signals = Array.isArray(payload) ? payload : (payload.signals ?? [])
  const hiddenHighConfidenceCount = Array.isArray(payload) ? 0 : (payload.hidden_high_confidence_count ?? 0)
  _cache.signals = signals
  _cache.hiddenHighConfidenceCount = hiddenHighConfidenceCount
  _listeners.forEach(fn => fn({ signals, hiddenHighConfidenceCount }))
}

// Treat cached data as fresh for 60 s with the same filters.
const STALE_MS = 60_000

export function useSignals() {
  const [signals, setSignals] = useState(_cache.signals)
  const [hiddenHighConfidenceCount, setHiddenCount] = useState(_cache.hiddenHighConfidenceCount)
  const [loading, setLoading]  = useState(false)
  const [error, setError]      = useState(null)
  const requestSeq             = useRef(0)

  // Stay in sync when another hook instance refreshes the cache
  useEffect(() => _subscribe(({ signals: s, hiddenHighConfidenceCount: h }) => {
    setSignals(s)
    setHiddenCount(h)
  }), [])

  const load = useCallback(async (filters = {}) => {
    const key = JSON.stringify(filters)
    const now = Date.now()

    // Return cached data immediately — no spinner on revisit
    if (_cache.signals.length > 0) {
      setSignals(_cache.signals)
      setHiddenCount(_cache.hiddenHighConfidenceCount)
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

  return { signals, hiddenHighConfidenceCount, loading, error, load, invalidate }
}
