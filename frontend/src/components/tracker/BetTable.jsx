import { useState } from 'react'
import { Download, Lock, Ticket, Bot, Pencil, Trash2, X, Clock, CheckCircle, AlertTriangle, MinusCircle } from 'lucide-react'
import { fmtK, fmtPL, fmtPLCompact } from '../../utils/format'
import { updateBet, deleteBet } from '../../api/tracker'

function fmtLegKickoff(isoStr) {
  if (!isoStr) return null
  const utc = isoStr.endsWith('Z') || isoStr.includes('+') ? isoStr : `${isoStr}Z`
  const d = new Date(utc)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', hour12: true,
  })
}

const LEG_RESULT_CFG = {
  won:     { icon: CheckCircle,   cls: 'text-green-400',  label: 'Won'  },
  lost:    { icon: AlertTriangle, cls: 'text-red-400',    label: 'Lost' },
  void:    { icon: MinusCircle,   cls: 'text-[var(--text)] opacity-60', label: 'Void' },
  pending: { icon: Clock,         cls: 'text-[var(--text)] opacity-50', label: null },
}

function LegResultBadge({ result, score }) {
  const cfg = LEG_RESULT_CFG[result] || LEG_RESULT_CFG.pending
  const Icon = cfg.icon
  if (result === 'pending' || !result) {
    return score ? <span className={`text-[10px] font-mono ${cfg.cls}`}>{score}</span> : null
  }
  return (
    <span className={`flex items-center gap-1 text-[10px] font-bold ${cfg.cls}`}>
      <Icon size={11} />
      {cfg.label}{score ? ` ${score}` : ''}
    </span>
  )
}

function escapeCsv(val) {
  if (val == null) return ''
  const s = String(val)
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"'
  }
  return s
}

function formatKickoff(isoStr) {
  if (!isoStr) return null
  const utc = isoStr.endsWith('Z') || isoStr.includes('+') ? isoStr : `${isoStr}Z`
  const d = new Date(utc)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', hour12: true })
}

function betsToCSV(bets) {
  const headers = [
    'Date', 'Kickoff', 'Match', 'League', 'Bookmaker', 'Market', 'Selection', 'Odds',
    'Stake', 'Profit/Loss', 'Result', 'Confidence', 'Rule Key', 'Notes',
    'Home Score', 'Away Score', 'CLV %', 'Closing Odds', 'Settled At',
  ]
  const rows = bets.map(b => {
    const matchName = b.home_team && b.away_team
      ? `${b.home_team} vs ${b.away_team}`
      : (b.match_name ?? '')
    return [
      b.event_date ?? '',
      formatKickoff(b.kickoff_at) ?? '',
      matchName,
      b.league ?? '',
      b.bookmaker ?? '',
      b.market_type ?? '',
      b.selection_name ?? '',
      b.odds != null ? b.odds.toFixed(2) : '',
      b.stake != null ? b.stake.toFixed(2) : '',
      b.profit_loss != null ? b.profit_loss.toFixed(2) : '',
      b.result_status ?? '',
      b.dual_confidence ?? '',
      b.source_rule_key ?? '',
      b.notes ?? '',
      b.home_score ?? '',
      b.away_score ?? '',
      b.clv_pct != null ? b.clv_pct.toFixed(2) : '',
      b.closing_odds != null ? b.closing_odds.toFixed(2) : '',
      b.settled_at ?? '',
    ].map(escapeCsv).join(',')
  })
  return [headers.join(','), ...rows].join('\r\n')
}

function downloadCSV(bets) {
  const csv = betsToCSV(bets)
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `titibet-bets-${new Date().toISOString().slice(0, 10)}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function formatEventDate(dateStr) {
  if (!dateStr) return '-'
  const d = new Date(`${dateStr}T00:00:00`)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatGroupDate(dateStr) {
  if (!dateStr) return 'Unknown Date'
  const d = new Date(`${dateStr}T00:00:00`)
  return d.toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
}

function groupByDate(bets) {
  const map = {}
  for (const bet of bets) {
    const key = bet.event_date ?? 'unknown'
    if (!map[key]) map[key] = []
    map[key].push(bet)
  }
  return Object.entries(map).sort(([a], [b]) => {
    if (a === 'unknown') return 1
    if (b === 'unknown') return -1
    return b > a ? 1 : b < a ? -1 : 0
  })
}

function actualPL(bet) {
  return bet.result_status === 'Won' || bet.result_status === 'Lost' || bet.result_status === 'Void'
    ? (bet.profit_loss ?? 0)
    : 0
}

function ScoreColumn({ homeScore, awayScore, fixtureStatus }) {
  const isFinal = fixtureStatus === 'FT' || fixtureStatus === 'AET' || fixtureStatus === 'PEN'

  if (homeScore != null && awayScore != null) {
    return (
      <div className="flex flex-col items-center gap-0.5">
        <span
          className="inline-flex items-center px-2.5 py-1 rounded-lg font-mono text-sm font-bold tabular-nums bg-[var(--accent)] text-white"
          title="Final score"
          style={{ letterSpacing: '0.04em' }}
        >
          {homeScore}-{awayScore}
        </span>
        <span className="text-[10px] text-[var(--text)] opacity-80 uppercase tracking-wide">
          {fixtureStatus ?? 'FT'}
        </span>
      </div>
    )
  }

  if (isFinal) {
    return (
      <div className="flex flex-col items-center gap-0.5">
        <span className="inline-flex items-center px-2 py-0.5 rounded font-mono text-[11px] font-semibold bg-[var(--code-bg)] border border-[var(--border)] text-[var(--text)] opacity-80">
          {fixtureStatus}
        </span>
        <span className="text-[10px] text-[var(--text)] opacity-70">no score</span>
      </div>
    )
  }

  return null
}

function CLVPill({ clvPct, closingOdds }) {
  if (clvPct == null) return <span className="text-[var(--text)] opacity-65 text-xs">No data</span>
  const positive = clvPct >= 0
  return (
    <div className="flex flex-col items-end">
      <span className={`text-xs font-bold font-mono ${positive ? 'text-green-400' : 'text-red-400'}`}>
        {positive ? '+' : ''}{clvPct.toFixed(1)}%
      </span>
      {closingOdds && (
        <span className="text-[10px] text-[var(--text)] opacity-75">
          closed {closingOdds.toFixed(2)}
        </span>
      )}
    </div>
  )
}

// ── Edit Bet modal ────────────────────────────────────────────────────────────
const RESULT_OPTIONS = ['Pending', 'Won', 'Lost', 'Void']

function EditBetModal({ bet, onClose, onSaved }) {
  const matchName = bet.home_team && bet.away_team
    ? `${bet.home_team} vs ${bet.away_team}`
    : bet.match_name

  const [stake, setStake]   = useState(String(bet.stake ?? ''))
  const [odds, setOdds]     = useState(String(bet.odds ?? ''))
  const [status, setStatus] = useState(bet.result_status ?? 'Pending')
  const [notes, setNotes]   = useState(bet.notes ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError]   = useState(null)

  const parsedStake = parseFloat(stake)
  const parsedOdds  = parseFloat(odds)
  const estimatedPL = Number.isFinite(parsedStake) && Number.isFinite(parsedOdds) && parsedOdds > 1
    ? status === 'Won'  ? parsedStake * (parsedOdds - 1)
    : status === 'Lost' ? -parsedStake
    : null
    : null

  async function handleSubmit(e) {
    e.preventDefault()
    if (!Number.isFinite(parsedStake) || parsedStake <= 0) { setError('Enter a valid stake.'); return }
    if (!Number.isFinite(parsedOdds)  || parsedOdds  <= 1) { setError('Enter valid odds > 1.'); return }
    setSaving(true)
    setError(null)
    try {
      await updateBet(bet.id, {
        stake: parsedStake,
        odds:  parsedOdds,
        result_status: status,
        notes: notes.trim() || null,
      })
      onSaved()
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-[var(--bg)] rounded-2xl border border-[var(--border)] shadow-xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border)]">
          <div>
            <h3 className="font-semibold text-[var(--text-h)]">Edit Bet</h3>
            <p className="text-xs text-[var(--text)] opacity-70 mt-0.5">{matchName} · {bet.market_type}</p>
          </div>
          <button onClick={onClose} className="text-[var(--text)] hover:text-[var(--text-h)]">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-5 py-4 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-sm text-[var(--text)] mb-1 block">Stake (K)</span>
              <input
                type="number" step="0.01" min="0.01"
                value={stake}
                onChange={e => setStake(e.target.value)}
                className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
                required
              />
            </label>
            <label className="block">
              <span className="text-sm text-[var(--text)] mb-1 block">Odds</span>
              <input
                type="number" step="0.001" min="1.01"
                value={odds}
                onChange={e => setOdds(e.target.value)}
                className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
                required
              />
            </label>
          </div>

          <label className="block">
            <span className="text-sm text-[var(--text)] mb-1 block">Result</span>
            <select
              value={status}
              onChange={e => setStatus(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]"
            >
              {RESULT_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>

          {estimatedPL !== null && (
            <div className={`px-3 py-2 rounded-lg text-sm font-mono font-semibold ${estimatedPL >= 0 ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
              New P&L: {fmtPL(estimatedPL)}
            </div>
          )}

          <label className="block">
            <span className="text-sm text-[var(--text)] mb-1 block">Notes</span>
            <textarea
              rows="2"
              value={notes}
              onChange={e => setNotes(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] resize-none"
            />
          </label>

          {error && <p className="text-xs text-red-400">{error}</p>}

          <div className="flex gap-2 pt-1">
            <button type="button" onClick={onClose}
              className="flex-1 px-4 py-2 rounded-lg border border-[var(--border)] text-sm text-[var(--text)] hover:bg-[var(--code-bg)] transition-colors">
              Cancel
            </button>
            <button type="submit" disabled={saving}
              className="flex-1 px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:opacity-90 disabled:opacity-60 transition-opacity">
              {saving ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Shared bet row renderer (used inside every source section) ────────────────
function BetRow({ bet, onRefresh }) {
  const [editing, setEditing]   = useState(false)
  const [deleting, setDeleting] = useState(false)

  const status = bet.result_status
  const pl = actualPL(bet)
  const isPending = status === 'Pending'
  const isWon     = status === 'Won'
  const isVoid    = status === 'Void'

  const badgeBg = isWon
    ? 'bg-green-600'
    : isPending
      ? 'bg-[var(--code-bg)] border border-[var(--border)]'
      : isVoid
        ? 'bg-slate-500'
        : 'bg-[#6b21a8]'
  const badgeText = isWon ? 'text-white' : isPending ? 'text-[var(--text)]' : 'text-white'
  const plText    = isPending ? '-' : fmtPLCompact(pl)
  const matchName = bet.home_team && bet.away_team
    ? `${bet.home_team} vs ${bet.away_team}`
    : bet.match_name

  async function handleDelete() {
    if (!window.confirm(`Delete this bet?\n${matchName} · ${bet.market_type}`)) return
    setDeleting(true)
    try {
      await deleteBet(bet.id)
      onRefresh?.()
    } catch (e) {
      alert(e.message)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <>
    {editing && (
      <EditBetModal bet={bet} onClose={() => setEditing(false)} onSaved={() => onRefresh?.()} />
    )}
    <div className="flex items-center justify-between px-4 py-3 bg-[var(--bg)] hover:bg-[var(--code-bg)] transition-colors gap-3">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-semibold text-[var(--text-h)]">{matchName}</span>
          <span className="text-sm font-bold text-[var(--accent)]">({bet.odds?.toFixed(2)})</span>
        </div>
        <div className="flex items-center gap-2 mt-0.5 text-xs text-[var(--text)] opacity-80 flex-wrap">
          {bet.source_rule_key === 'system_dual' && (
            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-emerald-500/15 text-emerald-400 border border-emerald-500/25 text-[10px] font-semibold">
              <Bot size={9} />
              Dual
            </span>
          )}
          {bet.source_rule_key === 'system_auto' && (
            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-violet-500/15 text-violet-400 border border-violet-500/25 text-[10px] font-semibold">
              <Bot size={9} />
              System
            </span>
          )}
          <span>{bet.market_type}</span>
          <span>·</span>
          <span>{bet.league}</span>
          <span>·</span>
          <span>{formatEventDate(bet.event_date)}</span>
          {formatKickoff(bet.kickoff_at) && (
            <>
              <span>·</span>
              <span className="font-mono font-semibold text-[var(--accent)] opacity-90">
                {formatKickoff(bet.kickoff_at)}
              </span>
            </>
          )}
        </div>
      </div>

      <div className="shrink-0 text-center hidden sm:flex flex-col items-center justify-center min-w-[80px]">
        <ScoreColumn
          homeScore={bet.home_score}
          awayScore={bet.away_score}
          fixtureStatus={bet.fixture_status}
        />
      </div>

      <div className="shrink-0 text-right hidden sm:block min-w-[70px]">
        <div className="text-[10px] text-[var(--text)] opacity-75 mb-0.5">CLV</div>
        <CLVPill clvPct={bet.clv_pct} closingOdds={bet.closing_odds} />
      </div>

      <div className="shrink-0 flex flex-col gap-1">
        <button
          onClick={() => setEditing(true)}
          title="Edit bet"
          className="p-1.5 rounded-md text-[var(--text)] opacity-50 hover:opacity-100 hover:bg-[var(--code-bg)] transition-all"
        >
          <Pencil size={13} />
        </button>
        <button
          onClick={handleDelete}
          disabled={deleting}
          title="Delete bet"
          className="p-1.5 rounded-md text-[var(--text)] opacity-50 hover:opacity-100 hover:text-red-400 hover:bg-red-500/10 disabled:opacity-30 transition-all"
        >
          <Trash2 size={13} />
        </button>
      </div>

      <div className={`shrink-0 rounded-lg px-4 py-2 text-center min-w-[110px] ${badgeBg}`}>
        <div className={`text-xs font-semibold ${badgeText}`}>{status}</div>
        <div className={`text-sm font-bold font-mono ${badgeText}`}>{plText}</div>
      </div>
    </div>
    </>
  )
}

// ── Accumulator row (expandable legs) ────────────────────────────────────────
function AccaRow({ bet, onRefresh }) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing]   = useState(false)
  const [deleting, setDeleting] = useState(false)

  const status = bet.result_status
  const pl = actualPL(bet)
  const isPending = status === 'Pending'
  const isWon     = status === 'Won'
  const isVoid    = status === 'Void'

  const badgeBg   = isWon ? 'bg-green-600' : isPending ? 'bg-[var(--code-bg)] border border-[var(--border)]' : isVoid ? 'bg-slate-500' : 'bg-[#6b21a8]'
  const badgeText = isWon ? 'text-white' : isPending ? 'text-[var(--text)]' : 'text-white'

  // Parse legs from JSON notes
  let legs = []
  try {
    const parsed = JSON.parse(bet.notes || '{}')
    legs = parsed.legs || []
  } catch (_) {}

  async function handleDelete() {
    if (!window.confirm(`Delete this accumulator?\n${bet.match_name}`)) return
    setDeleting(true)
    try { await deleteBet(bet.id); onRefresh?.() }
    catch (e) { alert(e.message) }
    finally { setDeleting(false) }
  }

  return (
    <>
      {editing && <EditBetModal bet={bet} onClose={() => setEditing(false)} onSaved={() => onRefresh?.()} />}
      <div>
        {/* Main row */}
        <div
          className="flex items-center justify-between px-4 py-3 bg-[var(--bg)] hover:bg-[var(--code-bg)] transition-colors gap-3 cursor-pointer"
          onClick={() => legs.length > 0 && setExpanded(v => !v)}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <Ticket size={13} className="text-[var(--accent)] shrink-0" />
              <span className="text-sm font-semibold text-[var(--text-h)]">{bet.match_name}</span>
              <span className="text-sm font-bold text-[var(--accent)]">(@{bet.odds?.toFixed(2)})</span>
              {legs.length > 0 && (
                <span className="text-[10px] text-[var(--text)] opacity-60">
                  {expanded ? '▲ hide legs' : '▼ show legs'}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-0.5 text-xs text-[var(--text)] opacity-80 flex-wrap">
              <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-[var(--accent)]/15 text-[var(--accent)] border border-[var(--accent)]/25 text-[10px] font-semibold">
                <Ticket size={9} /> Acca
              </span>
              <span>{formatEventDate(bet.event_date)}</span>
              <span>·</span>
              <span>K{Number(bet.stake).toLocaleString()}</span>
              {bet.dual_confidence && <><span>·</span><span>{bet.dual_confidence} confidence</span></>}
            </div>
          </div>

          <div className="shrink-0 flex flex-col gap-1" onClick={e => e.stopPropagation()}>
            <button onClick={() => setEditing(true)} title="Edit" className="p-1.5 rounded-md text-[var(--text)] opacity-50 hover:opacity-100 hover:bg-[var(--code-bg)] transition-all">
              <Pencil size={13} />
            </button>
            <button onClick={handleDelete} disabled={deleting} title="Delete" className="p-1.5 rounded-md text-[var(--text)] opacity-50 hover:opacity-100 hover:text-red-400 hover:bg-red-500/10 disabled:opacity-30 transition-all">
              <Trash2 size={13} />
            </button>
          </div>

          <div className={`shrink-0 rounded-lg px-4 py-2 text-center min-w-[110px] ${badgeBg}`}>
            <div className={`text-xs font-semibold ${badgeText}`}>{status}</div>
            <div className={`text-sm font-bold font-mono ${badgeText}`}>{isPending ? '-' : fmtPLCompact(pl)}</div>
          </div>
        </div>

        {/* Expanded legs */}
        {expanded && legs.length > 0 && (
          <div className="border-t border-[var(--border)] bg-[var(--code-bg)] px-4 py-3 space-y-2">
            {legs.map((leg, i) => {
              const match = leg.home_team && leg.away_team ? `${leg.home_team} vs ${leg.away_team}` : leg.match_name || '?'
              const ko = fmtLegKickoff(leg.kickoff_at)
              return (
                <div key={i} className="flex items-start gap-3 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2">
                  <span className="w-5 h-5 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] text-[10px] font-bold flex items-center justify-center shrink-0 mt-0.5">{i + 1}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-xs font-semibold text-[var(--text-h)]">{match}</span>
                      {leg.market && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-[var(--accent)]/15 text-[var(--accent)] font-semibold">{leg.market}</span>
                      )}
                    </div>
                    {ko && (
                      <p className="text-[10px] text-[var(--text)] opacity-60 flex items-center gap-1 mt-0.5">
                        <Clock size={9} />
                        {ko}
                      </p>
                    )}
                    {leg.reason && <p className="text-[11px] text-[var(--text)] opacity-70 leading-snug mt-0.5">{leg.reason}</p>}
                  </div>
                  <div className="shrink-0 flex flex-col items-end gap-1">
                    {leg.odd != null && <span className="text-xs font-bold text-[var(--text-h)] tabular-nums">{Number(leg.odd).toFixed(2)}</span>}
                    <LegResultBadge result={leg.result} score={leg.score} />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </>
  )
}

// ── Unified date-grouped view (accas + singles together per date) ─────────────
function UnifiedDateGroupedBets({ bets, onRefresh }) {
  const groups = groupByDate(bets)
  return (
    <div className="space-y-4">
      {groups.map(([dateKey, groupBets]) => {
        const groupPL = groupBets
          .filter(b => b.result_status === 'Won' || b.result_status === 'Lost')
          .reduce((sum, b) => sum + (b.profit_loss ?? 0), 0)
        const gColor = groupPL > 0 ? 'text-green-500' : groupPL < 0 ? 'text-red-500' : 'text-[var(--text)]'
        const pickCount = groupBets.length
        return (
          <div key={dateKey}>
            <div className="flex items-center justify-between px-4 py-2 mb-2 rounded-lg bg-[var(--accent-bg)] border border-[var(--accent-border)]">
              <span className="text-xs font-bold text-[var(--accent)] uppercase tracking-wider">
                {formatGroupDate(dateKey === 'unknown' ? null : dateKey)}
              </span>
              <span className="text-xs text-[var(--text)] opacity-75">
                {pickCount} {pickCount !== 1 ? 'picks' : 'pick'}
                {' · '}
                <span className={`font-mono font-semibold ${gColor}`} title={fmtPL(groupPL)}>
                  {fmtPLCompact(groupPL)}
                </span>
              </span>
            </div>
            <div className="rounded-xl border border-[var(--border)] overflow-hidden divide-y divide-[var(--border)]">
              {groupBets.map(bet =>
                bet.source_rule_key === 'acca_advisory'
                  ? <AccaRow key={bet.id} bet={bet} onRefresh={onRefresh} />
                  : <BetRow  key={bet.id} bet={bet} onRefresh={onRefresh} />
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function BetTable({ bets, isPro = true, onUpgrade, onRefresh }) {
  if (!bets.length) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-10 flex flex-col items-center gap-2 text-center">
        <span className="text-4xl">-</span>
        <p className="text-sm font-semibold text-[var(--text-h)]">No picks tracked yet</p>
        <p className="text-xs text-[var(--text)] opacity-75 max-w-xs">
          Go to <span className="font-semibold text-[var(--accent)]">Value Signals</span>, open a signal card, and tap <span className="font-semibold">Track Pick</span> to add bets here.
        </p>
      </div>
    )
  }

  const settled = bets.filter(b => b.result_status === 'Won' || b.result_status === 'Lost')
  const wins = settled.filter(b => b.result_status === 'Won')
  const netPL = settled.reduce((sum, bet) => sum + (bet.profit_loss ?? 0), 0)
  const totalStake = bets.reduce((sum, bet) => sum + (bet.stake ?? 0), 0)
  const strikeRate = settled.length ? (wins.length / settled.length) * 100 : 0
  const avgOdds = bets.length ? bets.reduce((sum, bet) => sum + (bet.odds ?? 0), 0) / bets.length : 0
  const profitPerPick = settled.length ? netPL / settled.length : 0

  const betsWithCLV = bets.filter(b => b.clv_pct != null)
  const avgCLV = betsWithCLV.length
    ? betsWithCLV.reduce((sum, bet) => sum + bet.clv_pct, 0) / betsWithCLV.length
    : null
  const positiveCLV = betsWithCLV.filter(b => b.clv_pct >= 0).length
  const clvCoverage = bets.length ? Math.round((betsWithCLV.length / bets.length) * 100) : 0

  const plColor = netPL > 0 ? 'text-green-500' : netPL < 0 ? 'text-red-500' : 'text-[var(--text-h)]'
  const clvColor = avgCLV == null ? 'text-[var(--text-h)]' : avgCLV >= 0 ? 'text-green-400' : 'text-red-400'

  return (
    <div className="space-y-6">
      {/* Ledger Summary */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 px-5 py-4 border-b border-[var(--border)] bg-[var(--code-bg)]">
          <span className="text-sm font-semibold text-[var(--text-h)]">Ledger Summary</span>
          <span className="text-xs text-[var(--text)] opacity-75 hidden sm:inline">
            KPIs below use recorded stake and settled profit/loss from the tracker ledger.
          </span>

          {isPro ? (
            <button
              onClick={() => downloadCSV(bets)}
              title="Download bets as CSV"
              className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors text-xs font-medium"
            >
              <Download size={13} />
              <span className="hidden sm:inline">Export CSV</span>
            </button>
          ) : (
            <button
              onClick={onUpgrade}
              title="Upgrade to Pro to export CSV"
              className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-blue-500/25 bg-blue-500/8 text-blue-400 text-xs font-medium cursor-pointer hover:bg-blue-500/15 transition-colors"
            >
              <Lock size={13} />
              <span className="hidden sm:inline">Export CSV · Pro</span>
            </button>
          )}
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-6 divide-y sm:divide-y-0 sm:divide-x divide-[var(--border)]">
          {[
            { label: 'Net P / L', value: fmtPLCompact(netPL), full: fmtPL(netPL), color: plColor },
            { label: 'Picks', value: bets.length, color: 'text-[var(--text-h)]' },
            { label: 'Settled', value: settled.length, color: 'text-[var(--text-h)]' },
            { label: 'Stake', value: fmtK(totalStake), color: 'text-[var(--text-h)]' },
            {
              label: 'Strike Rate',
              value: `${strikeRate.toFixed(1)}%`,
              subtitle: `Avg odds ${avgOdds.toFixed(2)} · P/P ${fmtPLCompact(profitPerPick)}`,
              color: 'text-[var(--text-h)]',
            },
            {
              label: 'Avg CLV',
              value: avgCLV != null ? `${avgCLV >= 0 ? '+' : ''}${avgCLV.toFixed(1)}%` : '-',
              color: clvColor,
              subtitle: betsWithCLV.length
                ? `${positiveCLV}/${betsWithCLV.length} beat close`
                : `${clvCoverage}% coverage`,
            },
          ].map(({ label, value, full, color, subtitle }) => (
            <div key={label} className="flex flex-col gap-0.5 px-4 py-4 min-w-0 overflow-hidden">
              <span className="text-xs text-[var(--text)] opacity-85 font-medium truncate">{label}</span>
              <span className={`text-lg font-bold font-mono truncate ${color}`} title={full}>{value}</span>
              {subtitle && (
                <span className="text-[10px] text-[var(--text)] opacity-75 leading-none truncate">{subtitle}</span>
              )}
            </div>
          ))}
        </div>

        {betsWithCLV.length > 0 && (
          <div className={`px-5 py-2.5 border-t border-[var(--border)] text-xs flex items-center gap-2 ${
            avgCLV != null && avgCLV >= 0
              ? 'bg-green-500/5 text-green-400'
              : 'bg-red-500/5 text-red-400'
          }`}>
            <span className="font-semibold">
              {avgCLV != null && avgCLV >= 0 ? 'Beating the closing line' : 'Not beating the closing line'}
            </span>
            <span className="opacity-75">
              CLV measures whether you consistently get better odds than the market closes at.
              Positive average CLV is stronger proof of real edge than short-term results alone.
            </span>
          </div>
        )}
      </div>

      <UnifiedDateGroupedBets bets={bets} onRefresh={onRefresh} />
    </div>
  )
}
