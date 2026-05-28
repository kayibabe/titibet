import { useState, useCallback, useRef } from 'react'
import { fetchStatPicks } from '../api/signals'

export function useStatPicks() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)
  const seq = useRef(0)

  const load = useCallback(async (date) => {
    const id = ++seq.current
    setLoading(true)
    setError(null)
    try {
      const next = await fetchStatPicks(date)
      if (id === seq.current) setData(next)
    } catch (e) {
      if (id === seq.current) setError(e.message)
    } finally {
      if (id === seq.current) setLoading(false)
    }
  }, [])

  return { data, loading, error, load }
}
