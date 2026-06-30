// Shared Value Bets primitives — the single source of truth for the value-bet
// card and odds tiers. Previously duplicated verbatim between ValueBetsPage
// and the Value Bets tab inside SignalsPage.

export const ODDS_TIERS = [
  { min: 1.5, label: '1.5+', desc: 'Conservative' },
  { min: 2.0, label: '2.0+', desc: 'Moderate'     },
  { min: 2.5, label: '2.5+', desc: 'Standard'     },
  { min: 3.0, label: '3.0+', desc: 'Value'        },
  { min: 3.5, label: '3.5+', desc: 'Aggressive'   },
  { min: 4.0, label: '4.0+', desc: 'Long shot'    },
]

function fmtKickoff(iso) {
  if (!iso) return null
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

const FINAL = new Set(['FT', 'AET', 'PEN'])

export default function ValueBetCard({ signal, rank }) {
  const b = signal.bayesian || {}
  const impliedProb = b.best_odd ? Math.round((1 / b.best_odd) * 100) : null
  const modelProb   = b.prob ? Math.round(b.prob * 100) : null
  const edge        = modelProb && impliedProb ? modelProb - impliedProb : null
  const evPct       = b.ev_pct ?? null

  const isFinal = FINAL.has(signal.status)

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden hover:border-[var(--accent)]/40 transition-colors">
      {/* Top stripe — EV colour coded */}
      <div className={`h-1 w-full ${evPct >= 50 ? 'bg-emerald-500' : evPct >= 20 ? 'bg-amber-400' : 'bg-blue-400'}`} />

      <div className="p-4 space-y-3">
        {/* Header row */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 mb-0.5">
              <span className="text-[10px] text-[var(--text)] opacity-70">
                {signal.country} · {signal.league}
              </span>
              {isFinal && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-500/20 text-slate-400 font-medium">FT</span>
              )}
            </div>
            <p className="text-sm font-semibold text-[var(--text-h)] leading-tight">
              {signal.home_team} vs {signal.away_team}
            </p>
            <p className="text-xs text-[var(--text)] opacity-70 mt-0.5">{signal.market}</p>
          </div>

          {/* Odds pill */}
          <div className="shrink-0 text-center">
            <div className="rounded-lg bg-[var(--accent)]/10 border border-[var(--accent)]/25 px-3 py-1.5">
              <p className="text-lg font-black text-[var(--accent)] tabular-nums leading-none">
                {b.best_odd?.toFixed(2) ?? '—'}
              </p>
              <p className="text-[9px] text-[var(--text)] opacity-70 mt-0.5">
                {b.bookmaker || 'Pinnacle'}
              </p>
            </div>
          </div>
        </div>

        {/* Metrics row */}
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-lg bg-[var(--code-bg)] p-2 text-center">
            <p className={`text-sm font-bold tabular-nums ${evPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {evPct != null ? `+${evPct.toFixed(1)}%` : '—'}
            </p>
            <p className="text-[9px] text-[var(--text)] opacity-70 mt-0.5">EV</p>
          </div>
          <div className="rounded-lg bg-[var(--code-bg)] p-2 text-center">
            <p className="text-sm font-bold tabular-nums text-[var(--text-h)]">
              {modelProb != null ? `${modelProb}%` : '—'}
            </p>
            <p className="text-[9px] text-[var(--text)] opacity-70 mt-0.5">Model Prob</p>
          </div>
          <div className="rounded-lg bg-[var(--code-bg)] p-2 text-center">
            <p className={`text-sm font-bold tabular-nums ${edge >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {edge != null ? `+${edge}pp` : '—'}
            </p>
            <p className="text-[9px] text-[var(--text)] opacity-70 mt-0.5">vs Implied</p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between text-[10px] text-[var(--text)] opacity-80">
          <div className="flex items-center gap-2">
            <span className={`px-1.5 py-0.5 rounded font-semibold ${
              signal.dual_confidence === 'High'   ? 'bg-emerald-500/15 text-emerald-400' :
              signal.dual_confidence === 'Medium' ? 'bg-amber-500/15 text-amber-400' :
                                                    'bg-slate-500/15 text-slate-400'
            }`}>{signal.dual_confidence}</span>
            <span className="px-1.5 py-0.5 rounded bg-[var(--code-bg)]">{signal.dual_agreement}</span>
          </div>
          {signal.kickoff_at && !isFinal && (
            <span>{fmtKickoff(signal.kickoff_at)}</span>
          )}
          {isFinal && signal.home_score != null && (
            <span className="font-semibold">{signal.home_score}–{signal.away_score}</span>
          )}
        </div>
      </div>
    </div>
  )
}
