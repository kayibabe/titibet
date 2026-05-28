import { useState, useCallback, useRef } from 'react'
import { fetchSignals } from '../api/signals'

export function useSignals() {
  const [signals, setSignals] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const requestSeq = useRef(0)

  const load = useCallback(async (filters = {}) => {
    const requestId = ++requestSeq.current
    setLoading(true)
    setError(null)
    try {
      const data = await fetchSignals(filters)
      if (requestId === requestSeq.current) {
        setSignals(data)
      }
    } catch (e) {
      if (requestId === requestSeq.current) {
        setError(e.message)
      }
    } finally {
      if (requestId === requestSeq.current) {
        setLoading(false)
      }
    }
  }, [])

  return { signals, loading, error, load }
}
