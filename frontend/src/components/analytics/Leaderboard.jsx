import { useState, useEffect } from 'react'
import { Trophy, Loader2, RefreshCw } from 'lucide-react'
import { fetchLeaderboard } from '../../api/analytics'

const MEDAL = ['🥇', '🥈', '🥉']

export default function Leaderboard() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  function load() {
    setLoading(true)
    setError(null)
    fetchLeaderboard()
      .then(d => setData(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  if (loading) return <div className="flex justify-center py-8"><Loader2 size={20} className="animate-spin text-[var(--accent)]" /></div>
  if (error)   return <p className="text-sm text-red-400 py-4">{error}</p>

  const entries = data?.entries ?? []

  if (entries.length === 0) {
    return (
      <div className="text-center py-10 space-y-2">
        <Trophy size={28} className="mx-auto text-amber-400 opacity-50" />
        <p className="text-sm text-[var(--text)] opacity-70">No entries yet — need {data?.min_bets ?? 5}+ settled bets to appear.</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-[var(--text)] opacity-55">
          Top {entries.length} users · min {data?.min_bets ?? 5} settled bets · pseudonymous
        </p>
        <button
          onClick={load}
          className="flex items-center gap-1 text-[10px] text-[var(--text)] opacity-50 hover:opacity-80 transition-opacity"
        >
          <RefreshCw size={10} /> Refresh
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[var(--border)] text-[var(--text)] opacity-60">
              <th className="px-3 py-2 text-left w-8">#</th>
              <th className="px-3 py-2 text-left">Bettor</th>
              <th className="px-3 py-2 text-right">Bets</th>
              <th className="px-3 py-2 text-right">Hit Rate</th>
              <th className="px-3 py-2 text-right">ROI</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e, i) => {
              const roiColor = e.roi >= 10 ? 'text-green-400' : e.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
              const hrColor  = e.win_rate >= 60 ? 'text-emerald-400' : e.win_rate >= 50 ? 'text-amber-400' : 'text-[var(--text-h)]'
              const rank = i + 1
              return (
                <tr key={i} className="border-t border-[var(--border)] hover:bg-[var(--code-bg)] transition-colors">
                  <td className="px-3 py-2.5 text-base leading-none">
                    {MEDAL[i] ?? <span className="font-mono text-[var(--text)] opacity-50">{rank}</span>}
                  </td>
                  <td className="px-3 py-2.5 font-mono font-semibold text-[var(--text-h)]">{e.name}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-[var(--text)]">{e.bets}</td>
                  <td className={`px-3 py-2.5 text-right tabular-nums font-semibold ${hrColor}`}>
                    {e.win_rate.toFixed(1)}%
                  </td>
                  <td className={`px-3 py-2.5 text-right tabular-nums font-semibold ${roiColor}`}>
                    {e.roi >= 0 ? '+' : ''}{e.roi.toFixed(1)}%
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] text-[var(--text)] opacity-40 text-center">
        Names are pseudonymous · only users with {data?.min_bets ?? 5}+ settled bets are ranked
      </p>
    </div>
  )
}
