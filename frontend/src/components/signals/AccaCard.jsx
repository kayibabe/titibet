import { useState, useEffect } from 'react'
import { Lock, ChevronDown, ChevronUp, Ticket } from 'lucide-react'
import { fetchAccumulators } from '../../api/accumulators'

function pickBestTicket(tiers) {
  if (!tiers) return null
  const all = Object.values(tiers)
  const complete = all.filter(t => !t.insufficient_picks && t.leg_count >= 3)
  if (complete.length) {
    return complete.sort((a, b) => b.expected_win_probability - a.expected_win_probability)[0]
  }
  const partial = all.filter(t => t.leg_count > 0)
  return partial.sort((a, b) => b.leg_count - a.leg_count)[0] || null
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
    <div className="rounded-xl border border-[var(--accent)]/25 bg-[var(--code-bg)] overflow-hidden">

      {/* ── Header ── */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-[var(--bg)]/50 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <Ticket size={16} className="text-[var(--accent)] shrink-0" />
          <span className="text-sm font-bold text-[var(--text-h)]">AI Acca of the Day</span>
          {isPartial && (
            <span className="text-[10px] font-semibold text-amber-400 border border-amber-400/40 rounded-full px-2 py-0.5">
              PARTIAL
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {winPct != null && (
            <span className="text-xs text-[var(--text)] opacity-50 tabular-nums">
              {winPct}% win prob
            </span>
          )}
          <span className="text-base font-extrabold text-[var(--accent)] tabular-nums">
            @ {ticket.combined_odds?.toFixed(2)}
          </span>
          {open
            ? <ChevronUp size={14} className="opacity-40 shrink-0" />
            : <ChevronDown size={14} className="opacity-40 shrink-0" />
          }
        </div>
      </button>

      {/* ── Legs ── */}
      {open && (
        <div className="border-t border-[var(--border)] divide-y divide-[var(--border)]">
          {legs.map((leg, i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-3">

              {/* Leg number */}
              <span className="w-5 h-5 shrink-0 flex items-center justify-center rounded-full bg-[var(--accent)]/10 text-[10px] font-extrabold text-[var(--accent)]">
                {i + 1}
              </span>

              {leg.locked ? (
                <div className="flex flex-1 items-center gap-2 opacity-40">
                  <Lock size={13} />
                  <span className="text-sm text-[var(--text)]">Upgrade to Pro to unlock this leg</span>
                </div>
              ) : (
                <>
                  {/* Match info */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-[var(--text-h)] truncate leading-snug">
                      {leg.home_team} vs {leg.away_team}
                    </p>
                    <p className="text-xs text-[var(--text)] opacity-50 truncate mt-0.5">
                      {leg.country && `${leg.country} · `}{leg.league}
                    </p>
                  </div>

                  {/* Market + odds */}
                  <div className="shrink-0 flex items-center gap-2.5">
                    <span className="text-[11px] font-bold tracking-wide uppercase px-2 py-1 rounded-md bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 whitespace-nowrap">
                      {leg.market}
                    </span>
                    <span className="text-base font-extrabold text-[var(--text-h)] tabular-nums w-10 text-right">
                      {leg.odd ? parseFloat(leg.odd).toFixed(2) : '—'}
                    </span>
                  </div>
                </>
              )}
            </div>
          ))}

          {isPartial && (
            <p className="text-xs text-[var(--text)] opacity-45 text-center px-4 py-3">
              Not enough qualifying picks today — check back after the 19:00 UTC sync
            </p>
          )}
        </div>
      )}
    </div>
  )
}
