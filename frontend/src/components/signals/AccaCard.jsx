import { useState, useEffect } from 'react'
import { Lock, ChevronDown, ChevronUp } from 'lucide-react'
import { fetchAccumulators } from '../../api/accumulators'

function pickBestTicket(tiers) {
  if (!tiers) return null
  const all = Object.values(tiers)
  // Prefer complete 3-leg tickets; among those, highest expected win probability
  const complete = all.filter(t => !t.insufficient_picks && t.leg_count >= 3)
  if (complete.length) {
    return complete.sort((a, b) => b.expected_win_probability - a.expected_win_probability)[0]
  }
  // Fall back to whatever has the most legs
  const partial = all.filter(t => t.leg_count > 0)
  return partial.sort((a, b) => b.leg_count - a.leg_count)[0] || null
}

function MarketPill({ market }) {
  return (
    <span className="inline-block text-[9px] font-bold tracking-wide uppercase px-1.5 py-0.5 rounded bg-[var(--accent)]/12 text-[var(--accent)] border border-[var(--accent)]/20 shrink-0">
      {market}
    </span>
  )
}

export default function AccaCard({ date }) {
  const [ticket, setTicket] = useState(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetchAccumulators(date)
      .then(data => setTicket(pickBestTicket(data.tiers)))
      .catch(() => setTicket(null))
      .finally(() => setLoading(false))
  }, [date])

  if (loading || !ticket || ticket.leg_count === 0) return null

  const legs = ticket.legs || []
  const winPct = ticket.expected_win_probability
    ? Math.round(ticket.expected_win_probability * 100)
    : null
  const isPartial = ticket.insufficient_picks

  return (
    <div className="rounded-xl border border-[var(--accent)]/20 bg-[var(--code-bg)] overflow-hidden">

      {/* ── Header ── */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-[var(--bg)]/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm leading-none select-none">🎟️</span>
          <span className="text-xs font-bold text-[var(--text-h)]">AI Acca of the Day</span>
          {isPartial && (
            <span className="text-[9px] text-amber-400 border border-amber-400/30 rounded px-1 py-0.5 font-semibold">
              PARTIAL
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {winPct != null && (
            <span className="text-[10px] text-[var(--text)] opacity-55 tabular-nums">
              {winPct}% win prob
            </span>
          )}
          <span className="text-sm font-bold text-[var(--accent)] tabular-nums">
            @ {ticket.combined_odds?.toFixed(2)}
          </span>
          {open ? <ChevronUp size={12} className="opacity-40" /> : <ChevronDown size={12} className="opacity-40" />}
        </div>
      </button>

      {/* ── Legs ── */}
      {open && (
        <div className="border-t border-[var(--border)] divide-y divide-[var(--border)]">
          {legs.map((leg, i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-2.5">
              <span className="text-[10px] font-bold text-[var(--text)] opacity-35 w-3.5 shrink-0 tabular-nums">
                {i + 1}
              </span>

              {leg.locked ? (
                <div className="flex flex-1 items-center gap-2 opacity-40">
                  <Lock size={10} />
                  <span className="text-[11px] text-[var(--text)]">Upgrade to Pro to unlock</span>
                </div>
              ) : (
                <>
                  <div className="flex-1 min-w-0">
                    <p className="text-[11px] font-semibold text-[var(--text-h)] truncate leading-snug">
                      {leg.home_team} vs {leg.away_team}
                    </p>
                    <p className="text-[9px] text-[var(--text)] opacity-50 truncate mt-0.5">
                      {leg.country && `${leg.country} · `}{leg.league}
                    </p>
                  </div>
                  <div className="shrink-0 flex flex-col items-end gap-1">
                    <MarketPill market={leg.market} />
                    <span className="text-xs font-bold text-[var(--text-h)] tabular-nums">
                      {leg.odd ? parseFloat(leg.odd).toFixed(2) : '—'}
                    </span>
                  </div>
                </>
              )}
            </div>
          ))}

          {isPartial && (
            <p className="text-[10px] text-[var(--text)] opacity-45 text-center px-4 py-2.5">
              Not enough qualifying picks today — check back after the 19:00 UTC sync
            </p>
          )}
        </div>
      )}
    </div>
  )
}
