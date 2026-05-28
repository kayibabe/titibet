/**
 * BetStatsBar — compact stats row above the bets table.
 *
 * Shows: win rate, current streak, ROI, best/worst market, P&L.
 * All computed client-side from the bets array — no extra API call.
 */
import { useMemo } from 'react'
import { fmtK } from '../../utils/format'

function computeStats(bets) {
  const settled = bets.filter(b => b.result_status !== 'Pending')
  if (settled.length === 0) return null

  const wins   = settled.filter(b => b.result_status === 'Won').length
  const losses = settled.filter(b => b.result_status === 'Lost').length
  const voids  = settled.filter(b => b.result_status === 'Void').length
  const totalPL = settled.reduce((sum, b) => sum + (b.profit_loss ?? 0), 0)
  const totalStake = settled.reduce((sum, b) => sum + (b.stake ?? 0), 0)
  const roi  = totalStake > 0 ? (totalPL / totalStake) * 100 : 0
  const wr   = settled.length > 0 ? (wins / (wins + losses || 1)) * 100 : 0

  // Current streak — walk from most-recent settled bet backwards
  const sorted = [...settled].sort((a, b) => {
    const ka = a.event_date || (a.settled_at ? String(a.settled_at).slice(0, 10) : '0000')
    const kb = b.event_date || (b.settled_at ? String(b.settled_at).slice(0, 10) : '0000')
    return ka < kb ? 1 : ka > kb ? -1 : 0
  })

  let streak = 0
  let streakType = null
  for (const b of sorted) {
    if (b.result_status === 'Void') continue // skip voids in streak
    if (streakType === null) {
      streakType = b.result_status   // 'Won' or 'Lost'
      streak = 1
    } else if (b.result_status === streakType) {
      streak++
    } else {
      break
    }
  }

  // Per-market P&L (top winner and top loser)
  const byMarket = {}
  for (const b of settled) {
    const m = b.market_type || 'Unknown'
    if (!byMarket[m]) byMarket[m] = { pl: 0, count: 0 }
    byMarket[m].pl += b.profit_loss ?? 0
    byMarket[m].count++
  }
  const marketEntries = Object.entries(byMarket).filter(([, v]) => v.count >= 2)
  const bestMarket  = marketEntries.sort(([, a], [, b]) => b.pl - a.pl)[0]
  const worstMarket = marketEntries.sort(([, a], [, b]) => a.pl - b.pl)[0]

  return {
    wins, losses, voids,
    totalPL: Math.round(totalPL * 100) / 100,
    roi: Math.round(roi * 10) / 10,
    wr: Math.round(wr * 1) / 1,
    streak, streakType,
    bestMarket:  bestMarket  ? { name: bestMarket[0],  pl: bestMarket[1].pl }  : null,
    worstMarket: worstMarket ? { name: worstMarket[0], pl: worstMarket[1].pl } : null,
  }
}

function StatCell({ label, value, sub, color }) {
  return (
    <div className="flex flex-col items-center gap-0.5 px-3 py-2 min-w-[60px]">
      <span className={`text-base font-bold tabular-nums leading-tight ${color || 'text-[var(--text-h)]'}`}>{value}</span>
      <span className="text-[10px] text-[var(--text)] opacity-80 text-center leading-tight">{label}</span>
      {sub && <span className="text-[10px] text-[var(--text)] opacity-65 text-center leading-tight">{sub}</span>}
    </div>
  )
}

export default function BetStatsBar({ bets }) {
  const stats = useMemo(() => computeStats(bets), [bets])
  if (!stats) return null

  const plColor   = stats.totalPL >= 0 ? 'text-green-400' : 'text-red-400'
  const roiColor  = stats.roi >= 0 ? 'text-green-400' : 'text-red-400'
  const streakColor = stats.streakType === 'Won' ? 'text-green-400' : stats.streakType === 'Lost' ? 'text-red-400' : 'text-[var(--text)]'
  const streakLabel = stats.streakType === 'Won' ? 'win streak' : stats.streakType === 'Lost' ? 'loss streak' : 'streak'

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] overflow-x-auto">
      <div className="flex items-stretch divide-x divide-[var(--border)] min-w-max">
        <StatCell
          label="Win Rate"
          value={`${stats.wr.toFixed(0)}%`}
          sub={`${stats.wins}W / ${stats.losses}L${stats.voids > 0 ? ` / ${stats.voids}V` : ''}`}
          color={stats.wr >= 50 ? 'text-green-400' : 'text-red-400'}
        />
        <StatCell
          label={streakLabel}
          value={stats.streak > 0 ? `${stats.streakType === 'Won' ? 'W' : 'L'}${stats.streak}` : '—'}
          color={streakColor}
        />
        <StatCell
          label="ROI"
          value={`${stats.roi >= 0 ? '+' : ''}${stats.roi.toFixed(1)}%`}
          color={roiColor}
        />
        <StatCell
          label="Total P&L"
          value={`${stats.totalPL >= 0 ? '+' : ''}${fmtK(stats.totalPL)}`}
          color={plColor}
        />
        {stats.bestMarket && (
          <div className="flex flex-col justify-center px-3 py-2 min-w-[120px]">
            <span className="text-[10px] text-[var(--text)] opacity-80">Best market</span>
            <span className="text-xs font-semibold text-green-400 truncate">{stats.bestMarket.name}</span>
            <span className="text-[10px] font-mono text-green-400">+{fmtK(stats.bestMarket.pl)}</span>
          </div>
        )}
        {stats.worstMarket && stats.worstMarket.name !== stats.bestMarket?.name && (
          <div className="flex flex-col justify-center px-3 py-2 min-w-[120px]">
            <span className="text-[10px] text-[var(--text)] opacity-80">Worst market</span>
            <span className="text-xs font-semibold text-red-400 truncate">{stats.worstMarket.name}</span>
            <span className="text-[10px] font-mono text-red-400">{fmtK(stats.worstMarket.pl)}</span>
          </div>
        )}
      </div>
    </div>
  )
}
