/**
 * LeagueInsights — best/worst league chips above the League Performance table.
 *
 * Shows top 3 and bottom 3 leagues by ROI (min 3 bets each) as coloured chips,
 * mirroring the MarketInsights component on the Market Performance section.
 */
export default function LeagueInsights({ rows }) {
  if (!rows || rows.length === 0) return null

  const eligible = rows.filter(r => ((r.wins ?? 0) + (r.losses ?? 0)) >= 3)
  if (eligible.length < 2) return null

  const sorted = [...eligible].sort((a, b) => b.roi - a.roi)
  const top    = sorted.slice(0, 3)
  const bottom = sorted.slice(-3).reverse()

  // Deduplicate so a league doesn't appear in both lists
  const topNames = new Set(top.map(r => r.league))
  const bottomFiltered = bottom.filter(r => !topNames.has(r.league))

  if (top.length === 0 && bottomFiltered.length === 0) return null

  function chip(r, type) {
    const isPos = type === 'top'
    const bg    = isPos ? 'bg-green-500/5 border-green-500/20'  : 'bg-red-500/5 border-red-500/20'
    const text  = isPos ? 'text-green-400' : 'text-red-400'
    const tag   = isPos ? '↑ ROI' : '↓ ROI'
    const name  = r.league || r.market || '—'

    return (
      <div key={name + type} className={`rounded-lg border ${bg} px-3 py-2 flex flex-col min-w-0`}>
        <p className={`text-[10px] font-semibold uppercase tracking-wide mb-0.5 ${text} opacity-75`}>{tag}</p>
        <p className={`text-sm font-bold ${text} truncate leading-tight`} title={name}>{name}</p>
        <p className="text-[11px] text-[var(--text)] opacity-70 mt-0.5 font-mono">
          {r.roi >= 0 ? '+' : ''}{r.roi?.toFixed(1)}% ROI
        </p>
        <p className="text-[10px] text-[var(--text)] opacity-70">
          {r.wins ?? 0}W · {r.losses ?? 0}L
          {r.win_rate != null ? ` · ${r.win_rate.toFixed(0)}% hit` : ''}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {/* Top leagues */}
      {top.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold text-[var(--text)] opacity-70 uppercase tracking-wide mb-1.5">
            Best performing leagues
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {top.map(r => chip(r, 'top'))}
          </div>
        </div>
      )}

      {/* Bottom leagues */}
      {bottomFiltered.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold text-[var(--text)] opacity-70 uppercase tracking-wide mb-1.5">
            Underperforming leagues
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {bottomFiltered.map(r => chip(r, 'bottom'))}
          </div>
        </div>
      )}
    </div>
  )
}
