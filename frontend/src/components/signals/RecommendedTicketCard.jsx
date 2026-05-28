import { useState } from 'react'
import {
  ChevronDown, ChevronUp, Target, Ticket, Shield, Gem, Flame,
  Star, Zap, Lock, TrendingDown,
} from 'lucide-react'

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(value) {
  if (value == null) return '—'
  return `${Math.round(value * 100)}%`
}

function odds(value) {
  if (value == null) return '—'
  return typeof value === 'number' ? value.toFixed(2) : String(value)
}

// ── Single leg row ────────────────────────────────────────────────────────────

function LegRow({ leg, greyed = false }) {
  return (
    <div className={`flex items-start justify-between gap-3 py-2.5 border-b border-[var(--border)] last:border-0 ${greyed ? 'opacity-40' : ''}`}>
      <div className="min-w-0 flex-1">
        <div className={`text-sm font-medium truncate ${greyed ? 'text-[var(--text)]' : 'text-[var(--text-h)]'}`}>
          {leg.match_name}
        </div>
        <div className="mt-0.5 text-xs text-[var(--text)]">
          {greyed ? (
            <span className="flex items-center gap-1">
              <Lock size={10} className="opacity-60" />
              <span className="opacity-60">Details hidden — upgrade to unlock</span>
            </span>
          ) : (
            <>
              {leg.market}
              {leg.bookmaker ? <span className="opacity-75"> · {leg.bookmaker}</span> : null}
            </>
          )}
        </div>
        {!greyed && leg.why_tags?.length > 0 && (
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            {leg.why_tags.slice(0, 3).map(tag => (
              <span
                key={tag}
                className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-[var(--code-bg)] text-[var(--text)] border border-[var(--border)]"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
      {!greyed && (
        <div className="shrink-0 text-right">
          <div className="font-mono text-sm font-semibold text-[var(--accent)]">{odds(leg.odds)}</div>
          <div className="text-[11px] text-[var(--text)] opacity-80">
            {leg.ev_pct != null ? `${leg.ev_pct >= 0 ? '+' : ''}${leg.ev_pct.toFixed(1)}% EV` : pct(leg.probability)}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Confidence/Win-prob badge ─────────────────────────────────────────────────

// ── General Ticket Card ───────────────────────────────────────────────────────

export function GeneralTicketCard({ ticket, onTrackAll, saving }) {
  const [expanded, setExpanded] = useState(false)
  const legs = ticket?.legs || []
  const isEmpty = legs.length === 0

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden shadow-sm">
      <div className="px-4 py-3 bg-[var(--code-bg)] border-b border-[var(--border)]">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-semibold border bg-[var(--accent)]/10 text-[var(--accent)] border-[var(--accent)]/30">
                <Ticket size={12} />
                TiTiBet General
              </span>
              <span className="text-xs text-[var(--text)] opacity-80">{legs.length} matches</span>
            </div>
            <p className="mt-1 text-[11px] text-[var(--text)] opacity-65">{ticket?.description}</p>
          </div>
          {!isEmpty && (
            <button
              onClick={() => setExpanded(v => !v)}
              className="flex items-center gap-1 text-xs text-[var(--text)] hover:text-[var(--text-h)] shrink-0"
            >
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              {expanded ? 'Hide' : 'View all'}
            </button>
          )}
        </div>
        {!isEmpty && (
          <div className="mt-2 flex items-center gap-3 flex-wrap">
            <span className="font-mono text-xl font-bold text-[var(--text-h)]">{odds(ticket.combined_odds)}x</span>
            <span className="text-xs text-[var(--text)] opacity-75">combined · Win prob {pct(ticket.win_probability_estimate)}</span>
          </div>
        )}
        {isEmpty && <p className="mt-2 text-sm text-[var(--text)] opacity-65">{ticket?.empty_reason || 'No signals yet for this date.'}</p>}
      </div>

      {!isEmpty && expanded && (
        <div className="px-4">
          {legs.map((leg, i) => <LegRow key={`gen-${leg.signal_id ?? i}`} leg={leg} />)}
        </div>
      )}

      {!isEmpty && (
        <div className="px-4 py-3 border-t border-[var(--border)] flex items-center gap-2">
          {onTrackAll ? (
            <button
              onClick={() => onTrackAll(legs)}
              disabled={saving}
              className="inline-flex items-center gap-1 rounded-lg bg-[var(--accent)] px-3 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60"
            >
              <Ticket size={14} />
              {saving ? 'Saving…' : 'Track Acca'}
            </button>
          ) : (
            <p className="text-xs text-[var(--text)] opacity-65">Log in to track these picks.</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Free Ticket Card ──────────────────────────────────────────────────────────

export function FreeTicketCard({ ticket, autoTracked, onAutoTrack, saving }) {
  const [expanded, setExpanded] = useState(true)
  const selected = ticket?.selected_legs || []
  const other    = ticket?.other_legs    || []
  const isEmpty  = selected.length === 0

  return (
    <div className="rounded-xl border border-emerald-500/30 bg-[var(--bg)] overflow-hidden shadow-sm">
      <div className="px-4 py-3 bg-emerald-500/6 border-b border-emerald-500/20">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-semibold border bg-emerald-500/12 text-emerald-300 border-emerald-500/30">
                <Star size={12} />
                TiTiBet Free
              </span>
              {autoTracked && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/25 font-medium">
                  ✓ Auto-tracked
                </span>
              )}
              {saving && (
                <span className="text-[10px] text-[var(--text)] opacity-65">Tracking…</span>
              )}
            </div>
            <p className="mt-1 text-[11px] text-[var(--text)] opacity-65">{ticket?.description}</p>
          </div>
          {!isEmpty && (
            <button
              onClick={() => setExpanded(v => !v)}
              className="flex items-center gap-1 text-xs text-[var(--text)] hover:text-[var(--text-h)] shrink-0"
            >
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          )}
        </div>
        {!isEmpty && (
          <div className="mt-2 flex items-center gap-3 flex-wrap">
            <span className="font-mono text-xl font-bold text-emerald-300">{odds(ticket.combined_odds)}x</span>
            <span className="text-xs text-[var(--text)] opacity-75">Win prob {pct(ticket.win_probability_estimate)}</span>
            {ticket.kelly_stake_pct != null && ticket.kelly_stake_pct > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded border border-emerald-500/30 text-emerald-400 font-semibold">
                Kelly {(ticket.kelly_stake_pct * 100).toFixed(1)}%
              </span>
            )}
          </div>
        )}
        {isEmpty && <p className="mt-2 text-sm text-[var(--text)] opacity-65">{ticket?.empty_reason || 'No signals yet for this date.'}</p>}
      </div>

      {!isEmpty && expanded && (
        <div className="px-4">
          {/* 3 selected picks — full detail */}
          {selected.length > 0 && (
            <div className="py-2">
              <p className="text-[10px] font-bold uppercase tracking-widest text-emerald-400 mb-1">Today's 3 Free Picks</p>
              {selected.map((leg, i) => <LegRow key={`free-sel-${leg.signal_id ?? i}`} leg={leg} />)}
            </div>
          )}
          {/* Other matches — greyed out */}
          {other.length > 0 && (
            <div className="py-2 border-t border-[var(--border)]">
              <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--text)] opacity-50 mb-1">
                Other matches today ({other.length}) — upgrade to unlock
              </p>
              {other.map((leg, i) => <LegRow key={`free-other-${leg.signal_id ?? i}`} leg={leg} greyed />)}
            </div>
          )}
        </div>
      )}

      {!isEmpty && !autoTracked && onAutoTrack && (
        <div className="px-4 py-3 border-t border-[var(--border)]">
          <button
            onClick={() => onAutoTrack(selected)}
            disabled={saving}
            className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60"
          >
            <Star size={14} />
            {saving ? 'Tracking…' : 'Track Free Picks'}
          </button>
        </div>
      )}
    </div>
  )
}

// ── Pro Sub-ticket Card ───────────────────────────────────────────────────────

const SUB_META = {
  high_conf_acca: { icon: Flame,        color: 'text-rose-400',   border: 'border-rose-500/30',   bg: 'bg-rose-500/6'   },
  goals_acca:     { icon: Zap,          color: 'text-amber-400',  border: 'border-amber-500/30',  bg: 'bg-amber-500/6'  },
  safe_ticket:    { icon: Shield,       color: 'text-cyan-400',   border: 'border-cyan-500/30',   bg: 'bg-cyan-500/6'   },
  best_singles:   { icon: Target,       color: 'text-violet-400', border: 'border-violet-500/30', bg: 'bg-violet-500/6' },
  sharp_moves:    { icon: TrendingDown, color: 'text-emerald-400',border: 'border-emerald-500/30',bg: 'bg-emerald-500/6'},
}

function ProSubTicket({ sub, onTrack, onTrackLeg, saving, savingLegs = {}, autoTracked = false }) {
  const isSingles = sub.key === 'best_singles' || sub.key === 'sharp_moves'
  // Best Singles and Sharp Moves auto-expand so per-leg track buttons are immediately visible
  const [expanded, setExpanded] = useState(isSingles)
  const meta    = SUB_META[sub.key] || { icon: Ticket, color: 'text-[var(--accent)]', border: 'border-[var(--border)]', bg: 'bg-[var(--code-bg)]' }
  const Icon    = meta.icon
  const legs    = sub.legs || []
  const isEmpty = !legs.length

  return (
    <div className={`rounded-lg border ${meta.border} overflow-hidden`}>
      <div className={`px-3 py-2.5 ${meta.bg} flex items-center justify-between gap-2`}>
        <div className="flex items-center gap-2 min-w-0">
          <Icon size={13} className={`${meta.color} shrink-0`} />
          <span className={`text-xs font-bold ${meta.color}`}>{sub.label}</span>
          {!isEmpty && (
            <span className="text-[10px] text-[var(--text)] opacity-65">{legs.length} {isSingles ? 'singles' : 'legs'}</span>
          )}
          {autoTracked && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${meta.color} ${meta.border} bg-transparent`}>
              ✓ Auto-tracked
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!isEmpty && !isSingles && (
            <span className="font-mono text-xs font-bold text-[var(--text-h)]">{odds(sub.combined_odds)}x</span>
          )}
          {!isEmpty && (
            <button
              onClick={() => setExpanded(v => !v)}
              className="text-[var(--text)] hover:text-[var(--text-h)]"
            >
              {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>
          )}
        </div>
      </div>

      {isEmpty && (
        <div className="px-3 py-2 text-xs text-[var(--text)] opacity-55">{sub.empty_reason}</div>
      )}

      {!isEmpty && expanded && (
        <div className="px-3">
          {legs.map((leg, i) => (
            <div key={`${sub.key}-${leg.signal_id ?? i}`} className="flex items-center gap-2">
              <div className="flex-1 min-w-0">
                <LegRow leg={leg} />
              </div>
              {isSingles && onTrackLeg && (
                <button
                  onClick={() => onTrackLeg(leg)}
                  disabled={savingLegs[leg.signal_id]}
                  className={`shrink-0 inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] font-semibold text-white hover:opacity-90 disabled:opacity-60 ${meta.color.replace('text-', 'bg-').replace('-400', '-600')}`}
                >
                  {savingLegs[leg.signal_id] ? '…' : 'Track'}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {!isEmpty && (
        <div className="px-3 py-2 border-t border-[var(--border)] flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            {sub.summary_tags?.slice(0, 2).map(t => (
              <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--code-bg)] text-[var(--text)] border border-[var(--border)]">{t}</span>
            ))}
            {sub.win_probability_estimate != null && !isSingles && (
              <span className="text-[10px] text-[var(--text)] opacity-60">Win {pct(sub.win_probability_estimate)}</span>
            )}
            {sub.kelly_stake_pct != null && !isSingles && sub.kelly_stake_pct > 0 && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded border font-semibold ${meta.color} ${meta.border}`}>
                Kelly {(sub.kelly_stake_pct * 100).toFixed(1)}%
              </span>
            )}
          </div>
          {!isSingles && !autoTracked && onTrack && (
            <button
              onClick={() => onTrack(sub)}
              disabled={saving}
              className={`inline-flex items-center gap-1 rounded px-2.5 py-1.5 text-[11px] font-semibold text-white hover:opacity-90 disabled:opacity-60 ${meta.color.replace('text-', 'bg-').replace('-400', '-600')}`}
            >
              {saving ? '…' : 'Track Acca'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Pro Ticket Card ───────────────────────────────────────────────────────────

export function ProTicketCard({ ticket, autoTracked, autoTrackedKeys = {}, onTrackSub, onTrackSubLeg, savingKeys = {}, savingLegs = {} }) {
  const subs = ticket?.sub_tickets || []

  return (
    <div className="rounded-xl border border-violet-500/30 bg-[var(--bg)] overflow-hidden shadow-sm">
      <div className="px-4 py-3 bg-violet-500/6 border-b border-violet-500/20">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-semibold border bg-violet-500/12 text-violet-300 border-violet-500/30">
            <Gem size={12} />
            TiTiBet Pro
          </span>
          {autoTracked && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-violet-500/15 text-violet-400 border border-violet-500/25 font-medium">
              ✓ Auto-tracked
            </span>
          )}
        </div>
        <p className="mt-1 text-[11px] text-[var(--text)] opacity-65">{ticket?.description}</p>
      </div>

      {subs.length === 0 ? (
        <div className="px-4 py-8 text-center text-sm text-[var(--text)] opacity-65">No signals available for Pro tickets today.</div>
      ) : (
        <div className="p-3 space-y-2">
          {subs.map(sub => {
            const isSinglesLike = sub.key === 'best_singles' || sub.key === 'sharp_moves'
            return (
              <ProSubTicket
                key={sub.key}
                sub={sub}
                onTrack={!isSinglesLike && onTrackSub ? (s) => onTrackSub(s) : undefined}
                onTrackLeg={isSinglesLike && onTrackSubLeg ? onTrackSubLeg : undefined}
                saving={savingKeys[sub.key] || false}
                savingLegs={savingLegs}
                autoTracked={autoTrackedKeys[sub.key] || false}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
