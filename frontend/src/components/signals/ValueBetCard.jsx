export const ACCUMULATOR_TIERS = [
  { odds: 1.5, label: '1.5x', desc: 'Conservative — 2–3 legs, high-probability picks' },
  { odds: 2.0, label: '2.0x', desc: 'Moderate — 3–4 legs, solid confidence' },
  { odds: 2.5, label: '2.5x', desc: 'Standard — 4–5 legs, balanced risk' },
  { odds: 3.0, label: '3.0x', desc: 'Value — 5–6 legs, meaningful upside' },
  { odds: 3.5, label: '3.5x', desc: 'Aggressive — 6–7 legs, higher variance' },
  { odds: 4.0, label: '4.0x', desc: 'Long shot — 7+ legs, maximum return' },
]

function fmtKickoff(iso) {
  if (!iso) return null
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

const FINAL = new Set(['FT', 'AET', 'PEN'])

export default function AccumulatorLegCard({ leg, index }) {
  const isFinal = FINAL.has((leg.status || '').trim().toUpperCase())
  const probPct = leg.primary_prob != null ? Math.round(leg.primary_prob * 100) : null

  return (
    <div className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--bg)] p-3.5 hover:border-[var(--accent)]/30 transition-colors">
      {/* Leg number */}
      <div className="shrink-0 w-6 h-6 rounded-full bg-[var(--accent)]/15 border border-[var(--accent)]/30 flex items-center justify-center">
        <span className="text-[10px] font-bold text-[var(--accent)]">{index + 1}</span>
      </div>

      {/* Match info */}
      <div className="flex-1 min-w-0">
        <p className="text-[10px] text-[var(--text)] opacity-60 truncate">
          {leg.country} · {leg.league}
        </p>
        <p className="text-sm font-semibold text-[var(--text-h)] leading-tight truncate">
          {leg.home_team} vs {leg.away_team}
        </p>
        <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
          <span className="text-[10px] font-medium text-[var(--text)] opacity-70 bg-[var(--code-bg)] px-1.5 py-0.5 rounded">
            {leg.market}
          </span>
          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
            leg.confidence === 'High'   ? 'bg-emerald-500/15 text-emerald-400' :
            leg.confidence === 'Medium' ? 'bg-amber-500/15 text-amber-400' :
                                          'bg-slate-500/15 text-slate-400'
          }`}>
            {leg.confidence}
          </span>
        </div>
      </div>

      {/* Odds + prob */}
      <div className="shrink-0 text-right space-y-1">
        <div className="text-base font-black text-[var(--accent)] tabular-nums leading-none">
          {leg.fair_odds?.toFixed(2)}
        </div>
        <div className="text-[9px] text-[var(--text)] opacity-60">fair odds</div>
        {probPct != null && (
          <div className="text-[10px] font-semibold text-[var(--text-h)] tabular-nums">{probPct}%</div>
        )}
        {!isFinal && leg.kickoff_at && (
          <div className="text-[9px] text-[var(--text)] opacity-50">{fmtKickoff(leg.kickoff_at)}</div>
        )}
        {isFinal && leg.home_score != null && (
          <div className="text-[10px] font-semibold text-slate-400">{leg.home_score}–{leg.away_score}</div>
        )}
      </div>
    </div>
  )
}
