import { useState, useCallback } from 'react'
import { fetchBets, fetchAccumulators } from '../api/tracker'

export function useTracker() {
  const [bets, setBets] = useState([])
  const [accumulators, setAccumulators] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const loadBets = useCallback(async (filters = {}) => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchBets(filters)
      setBets(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadAccumulators = useCallback(async () => {
    try {
      const data = await fetchAccumulators()
      setAccumulators(data)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  return { bets, accumulators, loading, error, loadBets, loadAccumulators }
}
