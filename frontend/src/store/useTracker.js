import { useState, useCallback } from 'react'
import { fetchBets } from '../api/tracker'

export function useTracker() {
  const [bets, setBets] = useState([])
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

  return { bets, loading, error, loadBets }
}
