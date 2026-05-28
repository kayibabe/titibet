/**
 * ConfidenceBreakdown — win rate, ROI and optional self-learning weight per confidence tier.
 *
 * Adds a visual win-rate bar so the reader can scan performance at a glance
 * without reading individual numbers.
 */

const CONF_META = {
  High:    { dot: 'bg-green-400',  label: 'text-green-400',  pill: 'bg-green-500/10 text-green-400 border-green-500/25',  bar: 'bg-green-400' },
  Medium:  { dot: 'bg-yellow-400', label: 'text-yellow-400', pill: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/25', bar: 'bg-yellow-400' },
  Low:     { dot: 'bg-orange-400', label: 'text-orange-400', pill: 'bg-orange-500/10 text-orange-400 border-orange-500/25', bar: 'bg-orange-400' },
  Unknown: { dot: 'bg-slate-400',  label: 'text-slate-400',  pill: 'bg-slate-500/10 text-slate-400 border-slate-500/25',  bar: 'bg-slate-400' },
}

function FactorBadge({ factor }) {
  if (factor == null) return null
  const isBoost   = factor > 1.05
  const isPenalty = factor < 0.95
  if (!isBoost && !isPenalty) return (
    <span className="text-[10px] text-[var(--text)] opacity-70 tabular-nums">1.00×</span>
  )
  const cls = isBoost
    ? 'bg-green-500/15 text-green-400 border-green-500/30'
    : 'bg-red-500/15 text-red-400 border-red-500/30'
  const arrow = isBoost ? '↑' : '↓'
  return (
    <span className={`inline-flex items-center gap-0.5 text-[10px] font-bold px-1.5 py-0.5 rounded border ${cls}`}>
      {arrow} {factor.toFixed(2)}×
    </span>
  )
}

function WinRateBar({ rate, barColor = 'bg-[var(--accent)]' }) {
  const pct = Math.min(100, Math.max(0, rate ?? 0))
  const color = pct >= 60 ? 'bg-green-400' : pct >= 45 ? 'bg-yellow-400' : 'bg-red-400'
  return (
    <div className="flex items-center gap-2 min-w-[80px]">
      <div className="flex-1 h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[11px] font-semibold tabular-nums w-10 text-right"
            style={{ color: pct >= 60 ? '#4ade80' : pct >= 45 ? '#facc15' : '#f87171' }}>
        {pct.toFixed(1)}%
      </span>
    </div>
  )
}

export default function ConfidenceBreakdown({ rows = [], title = 'Signal Confidence', showFactor = false, onFilterSignals }) {
  if (!rows.length) {
    return (
      <div>
        {title && <h3 className="text-sm font-semibold text-[var(--text-h)] mb-2">{title}</h3>}
        <p className="text-xs text-[var(--text)] opacity-65 py-4 text-center">No settled bets yet</p>
      </div>
    )
  }

  return (
    <div>
      <div className={`flex items-center justify-between ${title ? 'mb-3' : 'mb-2'}`}>
        {title && <h3 className="text-sm font-semibold text-[var(--text-h)]">{title}</h3>}
        <span className="text-[10px] text-[var(--text)] opacity-70 uppercase tracking-wide ml-auto">
          {rows.reduce((s, r) => s + (r.bets ?? 0), 0)} total bets
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[var(--text)] opacity-70 border-b border-[var(--border)]">
              <th className="px-3 py-2 text-left font-medium" colSpan={onFilterSignals ? 2 : 1}>Tier</th>
              <th className="px-3 py-2 text-right font-medium">Bets</th>
              <th className="px-3 py-2 text-right font-medium">W / L</th>
              <th className="px-3 py-2 text-left font-medium pl-4 min-w-[140px]">Hit Rate</th>
              <th className="px-3 py-2 text-right font-medium">ROI</th>
              <th className="px-3 py-2 text-right font-medium">P&L</th>
              {showFactor && <th className="px-3 py-2 text-right font-medium">Acca Weight</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const meta    = CONF_META[row.confidence] || CONF_META.Unknown
              const roiColor = row.roi >= 15 ? 'text-green-400' : row.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
              const plColor  = (row.profit_loss ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
              const pl = row.profit_loss != null
                ? `${row.profit_loss >= 0 ? '+' : ''}K${Math.abs(row.profit_loss).toFixed(2)}`
                : '—'

              return (
                <tr key={i} className="border-t border-[var(--border)] hover:bg-[var(--code-bg)] transition-colors group">
                  <td className="px-3 py-2.5">
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[11px] font-semibold ${meta.pill}`}>
                      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
                      {row.confidence}
                    </span>
                  </td>
                  {onFilterSignals && (
                    <td className="px-1 py-2.5 w-6">
                      <button
                        onClick={() => onFilterSignals({ confidence: row.confidence, label: `Confidence: ${row.confidence}` })}
                        title={`Filter signals to ${row.confidence} confidence`}
                        className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center text-[var(--accent)] p-0.5 rounded"
                      >
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>
                      </button>
                    </td>
                  )}
                  <td className="px-3 py-2.5 text-right text-[var(--text)]">{row.bets ?? '—'}</td>
                  <td className="px-3 py-2.5 text-right">
                    <span className="text-green-400">{row.wins ?? 0}</span>
                    <span className="text-[var(--text)] opacity-65 mx-1">/</span>
                    <span className="text-red-400">{row.losses ?? 0}</span>
                  </td>
                  <td className="px-3 py-2.5 pl-4">
                    <WinRateBar rate={row.win_rate} />
                  </td>
                  <td className={`px-3 py-2.5 text-right font-semibold tabular-nums ${roiColor}`}>
                    {row.roi != null ? `${row.roi >= 0 ? '+' : ''}${row.roi.toFixed(1)}%` : '—'}
                  </td>
                  <td className={`px-3 py-2.5 text-right font-mono tabular-nums ${plColor}`}>{pl}</td>
                  {showFactor && (
                    <td className="px-3 py-2.5 text-right">
                      <FactorBadge factor={row.performance_factor} />
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
