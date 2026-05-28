import { useState, useCallback, useRef } from 'react'
import { fetchRecommendedTickets } from '../api/signals'

export function useRecommendedTickets() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const requestSeq = useRef(0)

  const load = useCallback(async (date) => {
    const requestId = ++requestSeq.current
    setLoading(true)
    setError(null)
    try {
      const next = await fetchRecommendedTickets(date)
      if (requestId === requestSeq.current) {
        setData(next)
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

  return { data, loading, error, load }
}
