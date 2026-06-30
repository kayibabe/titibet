/**
 * BetStatsBar — compact stats row above the bets table.
 *
 * Shows: win rate, current streak, ROI, best/worst market, P&L.
 * Sourced from the backend analytics summary (build_analytics(), same
 * implementation the Analytics page uses) — TrackerPage fetches it with the
 * same filters as the bet list so the two pages never compute these numbers
 * via separate code paths.
 */
import { fmtK } from '../../utils/format'

function deriveStats(summary) {
  if (!summary || (summary.wins ?? 0) + (summary.losses ?? 0) === 0) return null

  const wins   = summary.wins ?? 0
  const losses = summary.losses ?? 0
  const voids  = Math.max(0, (summary.total_bets ?? 0) - (summary.settled_bets ?? 0) - (summary.pending_bets ?? 0))

  const eligible = (summary.by_market || []).filter(m => m.bets >= 2)
  const bestMarket  = eligible.length ? eligible.reduce((a, b) => (b.profit_loss > a.profit_loss ? b : a)) : null
  const worstMarket = eligible.length ? eligible.reduce((a, b) => (b.profit_loss < a.profit_loss ? b : a)) : null

  return {
    wins, losses, voids,
    totalPL: summary.total_profit_loss ?? 0,
    roi: summary.roi ?? 0,
    wr: summary.win_rate ?? 0,
    streak: summary.current_streak_len ?? 0,
    streakType: summary.current_streak_type ?? null,
    bestMarket:  bestMarket  ? { name: bestMarket.market,  pl: bestMarket.profit_loss }  : null,
    worstMarket: worstMarket ? { name: worstMarket.market, pl: worstMarket.profit_loss } : null,
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

export default function BetStatsBar({ summary }) {
  const stats = deriveStats(summary)
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
