/**
 * MarketHeatmap — visual win-rate + ROI grid for market performance.
 *
 * Each market row has:
 *   - A coloured ROI bar (green positive, red negative)
 *   - Win-rate dot colour (green ≥60%, amber 45-60%, red <45%)
 *   - Volume pill (number of bets)
 *
 * Markets with fewer than 2 bets are hidden (insufficient data).
 * Sorted by ROI descending.
 */
export default function MarketHeatmap({ rows }) {
  if (!rows || rows.length === 0) return null

  const eligible = rows
    .filter(r => ((r.wins ?? 0) + (r.losses ?? 0)) >= 2)
    .sort((a, b) => (b.roi ?? -Infinity) - (a.roi ?? -Infinity))

  if (eligible.length === 0) return null

  const maxAbsRoi = Math.max(...eligible.map(r => Math.abs(r.roi ?? 0)), 1)

  function roiBarWidth(roi) {
    return Math.min(100, (Math.abs(roi ?? 0) / maxAbsRoi) * 100)
  }

  function winRateDot(wr) {
    if (wr == null) return 'bg-slate-400'
    if (wr >= 60)   return 'bg-emerald-400'
    if (wr >= 45)   return 'bg-amber-400'
    return 'bg-red-400'
  }

  function roiColor(roi) {
    if (roi == null) return { bar: 'bg-slate-500/30', text: 'text-slate-400' }
    if (roi >= 15)  return { bar: 'bg-emerald-500/60', text: 'text-emerald-400' }
    if (roi >= 5)   return { bar: 'bg-green-500/45',   text: 'text-green-400' }
    if (roi >= 0)   return { bar: 'bg-green-500/25',   text: 'text-green-400/80' }
    if (roi >= -10) return { bar: 'bg-red-500/30',     text: 'text-red-400/80' }
    return             { bar: 'bg-red-500/55',         text: 'text-red-400' }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[10px] text-[var(--text)] opacity-70 px-1 mb-2">
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full bg-emerald-400" />≥60% hit rate</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full bg-amber-400" />45–60%</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full bg-red-400" />&lt;45%</span>
        </span>
        <span>bar = relative ROI magnitude</span>
      </div>

      {eligible.map(r => {
        const roi = r.roi ?? 0
        const wr  = r.win_rate
        const c   = roiColor(roi)
        const bets = (r.wins ?? 0) + (r.losses ?? 0) + (r.voids ?? 0)
        const barW = roiBarWidth(roi)

        return (
          <div key={r.market} className="flex items-center gap-2.5 group">
            {/* Win rate dot */}
            <div className={`w-2 h-2 rounded-full shrink-0 ${winRateDot(wr)}`} title={wr != null ? `${wr.toFixed(1)}% hit rate` : ''} />

            {/* Market name */}
            <span className="text-xs text-[var(--text-h)] w-36 sm:w-44 shrink-0 truncate group-hover:text-[var(--accent)] transition-colors" title={r.market}>
              {r.market}
            </span>

            {/* ROI bar */}
            <div className="flex-1 h-4 flex items-center">
              <div className="relative w-full h-2.5 rounded-full bg-[var(--border)] overflow-hidden">
                <div
                  className={`absolute h-full rounded-full transition-all ${c.bar} ${roi >= 0 ? 'left-0' : 'right-0'}`}
                  style={{ width: `${barW}%` }}
                />
              </div>
            </div>

            {/* ROI label */}
            <span className={`text-xs font-mono font-semibold w-14 text-right shrink-0 ${c.text}`}>
              {roi >= 0 ? '+' : ''}{roi.toFixed(1)}%
            </span>

            {/* Volume */}
            <span className="text-[10px] text-[var(--text)] opacity-65 w-8 text-right shrink-0 tabular-nums">{bets}</span>
          </div>
        )
      })}

      <p className="text-[10px] text-[var(--text)] opacity-65 pt-1 pl-1">
        Volume shown on right. Markets with &lt;2 settled bets hidden.
      </p>
    </div>
  )
}
