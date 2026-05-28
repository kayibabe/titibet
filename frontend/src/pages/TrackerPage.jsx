import { useState, useEffect, useMemo } from 'react'
import { RefreshCw, CheckCircle, TrendingUp, Lock, Ticket, Upload, Trash2 } from 'lucide-react'
import { useTracker } from '../store/useTracker'
import { syncData, computeCLV, fetchAccumulatorAnalytics, deduplicateAccumulators, deleteAccumulator } from '../api/tracker'
import { triggerAdminSettle } from '../api/admin'
import BetTable from '../components/tracker/BetTable'
import AccumulatorBuilder from '../components/tracker/AccumulatorBuilder'
import RecommendedTicketsTab from '../components/signals/RecommendedTicketsTab'
import PLChart from '../components/tracker/PLChart'
import BetStatsBar from '../components/tracker/BetStatsBar'
import ImportCSVModal from '../components/tracker/ImportCSVModal'
import LoadingSpinner from '../components/shared/LoadingSpinner'
import UpgradePrompt from '../components/shared/UpgradePrompt'
import DatePicker from '../components/shared/DatePicker'
import { fmtK, fmtPL, fmtPLCompact, fmtKCompact } from '../utils/format'
import useTier from '../hooks/useTier'

const STATUS_OPTIONS = ['', 'Pending', 'Won', 'Lost', 'Void']

// ── Date grouping helpers ─────────────────────────────────────────────────────
// Group key is the ticket's match day (ticket_date) when set, else the date
// portion of created_at. Both are returned by the API as YYYY-MM-DD strings
// (created_at is an ISO datetime, so we slice the leading 10 chars).
function accaDateKey(acca) {
  if (acca.ticket_date) return acca.ticket_date
  if (acca.created_at) return String(acca.created_at).slice(0, 10)
  return 'undated'
}

function todayKey() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function yesterdayKey() {
  const d = new Date()
  d.setDate(d.getDate() - 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatDateHeading(key) {
  if (key === 'undated') return 'Undated'
  if (key === todayKey()) return 'Today'
  if (key === yesterdayKey()) return 'Yesterday'
  // Parse as local midnight to avoid timezone-shift surprises around the date line.
  const d = new Date(`${key}T00:00:00`)
  if (Number.isNaN(d.getTime())) return key
  const today = new Date()
  const monthsAgo = (today.getFullYear() - d.getFullYear()) * 12 + (today.getMonth() - d.getMonth())
  return monthsAgo > 6
    ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    : d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

// Group recency tone — drives the colour of each date band so the eye can
// scan recency without reading. Tones intentionally match the rest of the app:
//   bg-{c}-500/10   — soft tint
//   border-{c}-500/20 — subtle edge
//   text-{c}-400    — readable on both light and dark surfaces
//   bg-{c}-400      — solid leading dot
function groupTone(key) {
  if (key === 'undated') {
    return { band: 'bg-amber-500/10 border-amber-500/20',  label: 'text-amber-400',  dot: 'bg-amber-400' }
  }
  if (key === todayKey()) {
    return { band: 'bg-violet-500/10 border-violet-500/30', label: 'text-violet-400', dot: 'bg-violet-400' }
  }
  if (key === yesterdayKey()) {
    return { band: 'bg-blue-500/10 border-blue-500/20',     label: 'text-blue-400',   dot: 'bg-blue-400' }
  }
  // How recent is this group? "This week" = within last 7 days, else "older".
  const d = new Date(`${key}T00:00:00`)
  const daysAgo = Number.isNaN(d.getTime())
    ? Infinity
    : Math.floor((Date.now() - d.getTime()) / 86_400_000)
  if (daysAgo <= 7) {
    return { band: 'bg-cyan-500/10 border-cyan-500/20',   label: 'text-cyan-400',  dot: 'bg-cyan-400' }
  }
  return   { band: 'bg-slate-500/10 border-slate-500/20', label: 'text-slate-300', dot: 'bg-slate-400' }
}

// ── Per-ticket card (extracted so we can render it inside each date group) ────
function TicketCard({ acca, onDelete }) {
  const [deleting, setDeleting] = useState(false)
  const pl = acca.profit_loss != null ? fmtPLCompact(acca.profit_loss) : '—'
  const plColor = acca.profit_loss > 0 ? 'text-green-400' : acca.profit_loss < 0 ? 'text-red-400' : 'text-[var(--text)]'
  const statusMeta = {
    Pending: { pill: 'bg-blue-500/10 text-blue-400 border-blue-500/20',   header: 'bg-blue-500/8  border-blue-500/20' },
    Won:     { pill: 'bg-green-500/10 text-green-400 border-green-500/20', header: 'bg-green-500/8 border-green-500/20' },
    Lost:    { pill: 'bg-red-500/10 text-red-400 border-red-500/20',       header: 'bg-red-500/8   border-red-500/20'  },
  }[acca.result_status] || { pill: 'bg-[var(--code-bg)] text-[var(--text)] border-[var(--border)]', header: 'bg-[var(--code-bg)] border-[var(--border)]' }

  async function handleDelete() {
    setDeleting(true)
    try { await onDelete(acca.id) } finally { setDeleting(false) }
  }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden">
      {/* Ticket header */}
      <div className={`px-4 py-3 flex items-center justify-between gap-3 flex-wrap border-b ${statusMeta.header}`}>
        <div>
          <div className="text-sm font-semibold text-[var(--text-h)]">
            {acca.name || `Ticket #${acca.id}`}
          </div>
          <div className="flex items-center gap-3 mt-1 text-xs text-[var(--text)] opacity-85">
            <span>{acca.legs?.length ?? 0} legs</span>
            <span>Stake: <span className="font-mono">{fmtK(acca.stake)}</span></span>
            <span>P/L: <span className={`font-mono font-semibold ${plColor}`}>{pl}</span></span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-xl font-bold text-[var(--accent)]">
            {acca.combined_odds?.toFixed(2)}
          </span>
          <span className={`text-xs px-2 py-1 rounded-full border font-medium ${statusMeta.pill}`}>
            {acca.result_status}
          </span>
          {onDelete && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              title="Remove this ticket"
              className="p-1 rounded text-[var(--text)] opacity-40 hover:opacity-100 hover:text-red-400 transition-opacity disabled:opacity-20"
            >
              <Trash2 size={13} className={deleting ? 'animate-pulse' : ''} />
            </button>
          )}
        </div>
      </div>
      {/* Legs */}
      {acca.legs?.length > 0 && (
        <div className="border-t border-[var(--border)] divide-y divide-[var(--border)]">
          {acca.legs.map((legObj, li) => {
            const b = legObj.bet
            const legStatus = {
              Pending: 'text-blue-400', Won: 'text-green-400', Lost: 'text-red-400',
            }[b?.result_status] || 'text-[var(--text)]'
            const hasScore = b?.home_score != null && b?.away_score != null
            return (
              <div key={li} className="px-4 py-2 flex items-center justify-between gap-3 text-xs">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-[var(--text-h)] truncate">{b?.match_name}</span>
                    {hasScore && (
                      <span
                        className="inline-flex items-center px-1.5 py-0.5 rounded font-mono text-[10px] font-bold bg-[var(--code-bg)] border border-[var(--border)] text-[var(--text-h)] tabular-nums shrink-0"
                        title="Final score"
                      >
                        {b.home_score}–{b.away_score}
                      </span>
                    )}
                  </div>
                  <span className="text-[var(--text)] opacity-75">{b?.market_type}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="font-mono text-[var(--accent)]">{b?.odds?.toFixed(2)}</span>
                  <span className={`font-medium ${legStatus}`}>{b?.result_status}</span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function TrackerPage({ user, settings, onUpgrade }) {
  const { isPro } = useTier()
  const today = (() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}` })()
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [activeTab, setActiveTab] = useState('recommended')
  const [syncing, setSyncing]           = useState(false)
  const [settling, setSettling]         = useState(false)
  const [settleResult, setSettleResult] = useState(null)   // { settled: number } | null
  const [computingCLV, setComputingCLV] = useState(false)
  const [showImport, setShowImport]     = useState(false)
  const [clvResult, setClvResult]       = useState(null)
  const [accaStats, setAccaStats]       = useState(null)
  const [deduping, setDeduping]         = useState(false)
  const [dedupResult, setDedupResult]   = useState(null)
  const { bets, accumulators, loading, error, loadBets, loadAccumulators } = useTracker()

  // Declare early — used in useEffect dependency arrays below
  const pendingCount = bets.filter(b => b.result_status === 'Pending').length
  const noCLVCount   = bets.filter(b => b.fixture_id && b.clv_pct == null).length

  const betFilters = { date_from: dateFrom || undefined, date_to: dateTo || undefined, result_status: statusFilter || undefined }

  useEffect(() => {
    loadBets(betFilters)
  }, [dateFrom, dateTo, statusFilter, loadBets]) // eslint-disable-line

  useEffect(() => {
    loadAccumulators()
    if (isPro) {
      fetchAccumulatorAnalytics().then(setAccaStats).catch(() => null)
    }
  }, [loadAccumulators, isPro])

  // ── Auto-refresh when there are pending bets ────────────────────────────────
  // 1. Reload when the browser tab regains focus — catches backend auto-settlement
  useEffect(() => {
    function handleVisibility() {
      if (document.visibilityState === 'visible' && pendingCount > 0) {
        loadBets(betFilters)
        loadAccumulators()
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [pendingCount]) // eslint-disable-line

  // 2. Silent poll every 5 min while any bets are still pending
  useEffect(() => {
    if (pendingCount === 0) return
    const id = setInterval(() => {
      loadBets(betFilters)
      loadAccumulators()
    }, 5 * 60 * 1000)
    return () => clearInterval(id)
  }, [pendingCount]) // eslint-disable-line

  async function handleSync() {
    setSyncing(true)
    try { await syncData() } catch (e) { console.error(e) } finally { setSyncing(false) }
    await loadBets({ date_from: dateFrom || undefined, date_to: dateTo || undefined, result_status: statusFilter || undefined })
  }

  async function handleSettle() {
    setSettling(true)
    setSettleResult(null)
    try {
      const res = await triggerAdminSettle()
      setSettleResult(res)                 // { settled: number }
      // Auto-clear the toast after 6 s
      setTimeout(() => setSettleResult(null), 6000)
    } catch (e) {
      console.error('Settle error:', e)
    } finally {
      setSettling(false)
    }
    // Reload bets + accumulators so the UI reflects settled results
    await Promise.all([
      loadBets({ date_from: dateFrom || undefined, date_to: dateTo || undefined, result_status: statusFilter || undefined }),
      loadAccumulators(),
    ])
  }

  async function handleComputeCLV() {
    setComputingCLV(true)
    setClvResult(null)
    try {
      const result = await computeCLV()
      setClvResult(result)
      await loadBets({ date_from: dateFrom || undefined, date_to: dateTo || undefined, result_status: statusFilter || undefined })
    } catch (e) { console.error(e) }
    finally { setComputingCLV(false) }
  }

  async function handleDeleteAccumulator(id) {
    await deleteAccumulator(id)
    await loadAccumulators()
    if (isPro) fetchAccumulatorAnalytics().then(setAccaStats).catch(() => null)
  }

  async function handleDeduplicate() {
    setDeduping(true)
    setDedupResult(null)
    try {
      const res = await deduplicateAccumulators()
      setDedupResult(res.removed)
      await loadAccumulators()
      if (isPro) fetchAccumulatorAnalytics().then(setAccaStats).catch(() => null)
    } catch (e) { console.error(e) }
    finally { setDeduping(false) }
  }

  // Map each tracked-bet id → its ticket source so BetTable can group by origin.
  // Falls back to name-based inference for tickets created before ticket_source existed.
  const betSourceMap = useMemo(() => {
    const map = {}
    for (const acca of accumulators) {
      const src = acca.ticket_source ||
        (acca.name?.toLowerCase().includes('recommended') ? 'ai_ticket' :
         acca.name?.toLowerCase().includes('auto acca')   ? 'goals_acca' : 'manual')
      for (const legObj of (acca.legs || [])) {
        const betId = legObj.bet?.id
        if (betId != null) {
          map[betId] = { source: src, ticketName: acca.name, ticketId: acca.id }
        }
      }
    }
    return map
  }, [accumulators])

  // Group accumulators by date — newest day first, "undated" pinned to the bottom.
  // Within each day, the API's `created_at desc` ordering is preserved.
  const groupedAccas = useMemo(() => {
    const groups = new Map()
    for (const a of accumulators) {
      const k = accaDateKey(a)
      if (!groups.has(k)) groups.set(k, [])
      groups.get(k).push(a)
    }
    return [...groups.entries()].sort(([a], [b]) => {
      if (a === 'undated') return 1
      if (b === 'undated') return -1
      return b.localeCompare(a)
    })
  }, [accumulators])

  return (
    <div className="space-y-6">
      {/* Toolbar */}
      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={handleSync} disabled={syncing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-sm text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] disabled:opacity-50 transition-colors">
          <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} />
          {syncing ? 'Syncing…' : 'Sync'}
        </button>
        <button onClick={handleSettle} disabled={settling || pendingCount === 0}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity">
          <CheckCircle size={13} className={settling ? 'animate-pulse' : ''} />
          <span className="hidden sm:inline">{settling ? 'Settling…' : `Settle (${pendingCount})`}</span>
          <span className="sm:hidden">{settling ? '…' : `Settle ${pendingCount > 0 ? `(${pendingCount})` : ''}`}</span>
        </button>

        {/* Settlement result toast */}
        {settleResult != null && (() => {
          const r = settleResult
          const skippedNoFix  = r.skip_no_fixture  ?? 0
          const skippedNFinal = r.skip_not_final   ?? 0
          const skippedScore  = r.skip_no_score    ?? 0
          const skippedMkt    = r.skip_no_market   ?? 0
          const anySkip = skippedNoFix + skippedNFinal + skippedScore + skippedMkt > 0
          return (
            <span className="text-xs font-medium flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
              <span className="text-emerald-400">
                ✓ {r.settled} settled
                {r.voided > 0 && ` · ${r.voided} voided`}
                {r.refreshed_fixtures > 0 && ` · ${r.refreshed_fixtures} refreshed`}
                {r.api_calls_made != null && ` · ${r.api_calls_made} API call${r.api_calls_made !== 1 ? 's' : ''}`}
              </span>
              {r.quota?.remaining != null && (
                <span className={r.quota.remaining <= 5 ? 'text-red-400' : 'text-slate-400'}>
                  · {r.quota.remaining} quota left
                </span>
              )}
              {r.errors > 0 && (
                <span className="text-amber-400">· {r.errors} fixture fetch errors</span>
              )}
              {anySkip && (
                <span className="text-slate-400 text-[10px]">
                  (skipped:
                  {skippedNoFix  > 0 && ` ${skippedNoFix} no fixture link`}
                  {skippedNFinal > 0 && ` · ${skippedNFinal} not finished`}
                  {skippedScore  > 0 && ` · ${skippedScore} awaiting score`}
                  {skippedMkt    > 0 && ` · ${skippedMkt} unknown market`}
                  )
                </span>
              )}
            </span>
          )
        })()}

        {/* CLV button — Pro only */}
        {isPro ? (
          <button
            onClick={handleComputeCLV}
            disabled={computingCLV}
            title="Compute Closing Line Value for all tracked bets"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-green-500/40 text-green-400 text-sm font-semibold hover:bg-green-500/10 disabled:opacity-50 transition-colors"
          >
            <TrendingUp size={13} className={computingCLV ? 'animate-pulse' : ''} />
            <span className="hidden sm:inline">{computingCLV ? 'Computing CLV…' : noCLVCount > 0 ? `Compute CLV (${noCLVCount})` : 'Refresh CLV'}</span>
            <span className="sm:hidden">CLV</span>
          </button>
        ) : (
          <div
            title="Upgrade to Pro to compute Closing Line Value"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-blue-500/25 bg-blue-500/8 text-blue-400 text-sm cursor-default"
          >
            <Lock size={13} />
            <span className="hidden sm:inline">CLV · Pro</span>
            <span className="sm:hidden">CLV</span>
          </div>
        )}

        {/* CLV result toast */}
        {clvResult && (
          <span className="text-xs text-green-400 font-medium">
            ✓ {clvResult.updated} updated
            {clvResult.skipped_no_data > 0 && <span className="hidden sm:inline"> · {clvResult.skipped_no_data} no data</span>}
          </span>
        )}

        {/* Import CSV */}
        <button
          onClick={() => setShowImport(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-sm text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] transition-colors ml-auto"
          title="Bulk-import historical bets from CSV"
        >
          <Upload size={13} />
          <span className="hidden sm:inline">Import CSV</span>
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex gap-0.5 border-b border-[var(--border)] overflow-x-auto">
        {[
          { id: 'recommended',   label: 'Recommended Tickets', icon: Ticket  },
          { id: 'bets',          label: 'Bets',                icon: null    },
          { id: 'accumulators',  label: 'Accumulators',        icon: null    },
        ].map(({ id, label, icon: Icon }) => {
          const locked = id === 'accumulators' && !isPro
          return (
            <button key={id} onClick={() => setActiveTab(id)}
              className={`shrink-0 flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                activeTab === id
                  ? 'border-[var(--accent)] text-[var(--accent)]'
                  : 'border-transparent text-[var(--text)] hover:text-[var(--text-h)]'
              }`}>
              {Icon && <Icon size={13} />}
              {label}
              {locked && (
                <span className="text-[9px] font-bold text-blue-400 bg-blue-500/15 border border-blue-500/30 px-1 py-0.5 rounded tracking-wide leading-none">
                  PRO
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* ── RECOMMENDED TICKETS TAB ─────────────────────────────────────────── */}
      {activeTab === 'recommended' && (
        <RecommendedTicketsTab
          date={today}
          settings={settings}
          isPro={isPro}
          onSwitchTab={setActiveTab}
        />
      )}

      {activeTab === 'bets' && (
        <div className="space-y-4">
          {/* Filter bar */}
          <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3">
            <div className="grid grid-cols-2 sm:flex sm:items-end gap-3 sm:gap-4">
              <DatePicker label="From" value={dateFrom} onChange={setDateFrom} />
              <DatePicker label="To" value={dateTo} onChange={setDateTo} />
              <label className="flex flex-col gap-1 text-sm text-[var(--text)]">
                <span className="font-medium">Status</span>
                <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
                  className="w-full px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]">
                  {STATUS_OPTIONS.map(o => <option key={o} value={o}>{o || 'All'}</option>)}
                </select>
              </label>
            </div>
          </div>

          {/* P&L chart + stats bar — visible once there are settled bets */}
          {!loading && bets.some(b => b.result_status !== 'Pending') && (
            <>
              <BetStatsBar bets={bets} />
              <PLChart bets={bets} />
            </>
          )}

          {loading && <div className="flex justify-center py-8"><LoadingSpinner /></div>}
          {error && <p className="text-sm text-red-400">{error}</p>}
          {!loading && <BetTable bets={bets} betSourceMap={betSourceMap} isPro={isPro} onUpgrade={onUpgrade} />}
        </div>
      )}

      {activeTab === 'accumulators' && (
        <div className="space-y-4">
          {/* Accumulator stats — Pro only, shown when there's data */}
          {isPro && accaStats && accaStats.settled_tickets > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: 'Hit Rate', value: `${accaStats.hit_rate?.toFixed(1)}%`, sub: `${accaStats.wins}W / ${accaStats.losses}L`, highlight: accaStats.hit_rate >= 20 },
                { label: 'ROI', value: `${accaStats.roi >= 0 ? '+' : ''}${accaStats.roi?.toFixed(1)}%`, sub: 'on settled tickets', highlight: accaStats.roi >= 0 },
                { label: 'P&L', value: fmtPLCompact(accaStats.total_profit_loss), full: fmtPL(accaStats.total_profit_loss), sub: `${accaStats.settled_tickets} settled`, highlight: accaStats.total_profit_loss > 0 },
                { label: 'Tickets', value: accaStats.total_tickets, sub: `${accaStats.pending_tickets} pending`, highlight: false },
              ].map((kpi, i) => (
                <div key={i} className="rounded-xl border border-[var(--border)] bg-[var(--bg)] px-4 py-3 flex flex-col gap-0.5 min-w-0 overflow-hidden">
                  <span className="text-xs text-[var(--text)] opacity-85 truncate">{kpi.label}</span>
                  <span className={`text-xl font-semibold truncate ${kpi.highlight ? 'text-[var(--accent)]' : 'text-[var(--text-h)]'}`} title={kpi.full}>{kpi.value}</span>
                  {kpi.sub && <span className="text-xs text-[var(--text)] opacity-70 truncate">{kpi.sub}</span>}
                </div>
              ))}
            </div>
          )}

          {/* Leg-count and odds-band breakdowns — shown when enough data */}
          {isPro && accaStats && accaStats.by_legs?.length > 0 && (
            <div className="grid sm:grid-cols-2 gap-4">
              <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4">
                <h4 className="text-xs font-semibold text-[var(--text-h)] mb-2 uppercase tracking-wide opacity-70">By Leg Count</h4>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-[var(--text)] opacity-75 border-b border-[var(--border)]">
                      <th className="pb-1.5 text-left font-medium">Legs</th>
                      <th className="pb-1.5 text-right font-medium">Tickets</th>
                      <th className="pb-1.5 text-right font-medium">Hit Rate</th>
                      <th className="pb-1.5 text-right font-medium">ROI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accaStats.by_legs.map((row, i) => (
                      <tr key={i} className="border-t border-[var(--border)]">
                        <td className="py-1.5 font-medium text-[var(--text-h)]">{row.legs}-leg</td>
                        <td className="py-1.5 text-right text-[var(--text)]">{row.tickets}</td>
                        <td className={`py-1.5 text-right font-semibold ${row.hit_rate >= 20 ? 'text-green-400' : 'text-[var(--text-h)]'}`}>{row.hit_rate?.toFixed(1)}%</td>
                        <td className={`py-1.5 text-right font-semibold ${row.roi >= 0 ? 'text-green-400' : 'text-red-400'}`}>{row.roi >= 0 ? '+' : ''}{row.roi?.toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4">
                <h4 className="text-xs font-semibold text-[var(--text-h)] mb-2 uppercase tracking-wide opacity-70">By Odds Band</h4>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-[var(--text)] opacity-75 border-b border-[var(--border)]">
                      <th className="pb-1.5 text-left font-medium">Band</th>
                      <th className="pb-1.5 text-right font-medium">Tickets</th>
                      <th className="pb-1.5 text-right font-medium">Hit Rate</th>
                      <th className="pb-1.5 text-right font-medium">ROI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accaStats.by_odds_band.map((row, i) => (
                      <tr key={i} className="border-t border-[var(--border)]">
                        <td className="py-1.5 font-medium text-[var(--text-h)]">{row.band}</td>
                        <td className="py-1.5 text-right text-[var(--text)]">{row.tickets}</td>
                        <td className={`py-1.5 text-right font-semibold ${row.hit_rate >= 20 ? 'text-green-400' : 'text-[var(--text-h)]'}`}>{row.hit_rate?.toFixed(1)}%</td>
                        <td className={`py-1.5 text-right font-semibold ${row.roi >= 0 ? 'text-green-400' : 'text-red-400'}`}>{row.roi >= 0 ? '+' : ''}{row.roi?.toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Market combo breakdown — which market combinations win most */}
          {isPro && accaStats && accaStats.by_source?.length > 0 && (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4">
              <h4 className="text-xs font-semibold text-[var(--text-h)] mb-2 uppercase tracking-wide opacity-70">
                Performance By Ticket Source
              </h4>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[var(--text)] opacity-75 border-b border-[var(--border)]">
                    <th className="pb-1.5 text-left font-medium">Source</th>
                    <th className="pb-1.5 text-right font-medium">Tickets</th>
                    <th className="pb-1.5 text-right font-medium">Hit Rate</th>
                    <th className="pb-1.5 text-right font-medium">ROI</th>
                    <th className="pb-1.5 text-right font-medium">P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {accaStats.by_source.map((row, i) => (
                    <tr key={i} className="border-t border-[var(--border)]">
                      <td className="py-1.5 font-medium text-[var(--text-h)]">{row.source}</td>
                      <td className="py-1.5 text-right text-[var(--text)]">{row.tickets}</td>
                      <td className={`py-1.5 text-right font-semibold ${row.hit_rate >= 20 ? 'text-green-400' : 'text-[var(--text-h)]'}`}>
                        {row.hit_rate?.toFixed(1)}%
                      </td>
                      <td className={`py-1.5 text-right font-semibold ${row.roi >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {row.roi >= 0 ? '+' : ''}{row.roi?.toFixed(1)}%
                      </td>
                      <td className={`py-1.5 text-right font-semibold font-mono ${row.profit_loss >= 0 ? 'text-green-400' : 'text-red-400'}`} title={fmtPL(row.profit_loss)}>
                        {fmtPLCompact(row.profit_loss)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {isPro && accaStats && accaStats.by_market_combo?.length > 0 && (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4">
              <h4 className="text-xs font-semibold text-[var(--text-h)] mb-2 uppercase tracking-wide opacity-70">
                Winning Market Combos
              </h4>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[var(--text)] opacity-75 border-b border-[var(--border)]">
                    <th className="pb-1.5 text-left font-medium">Markets</th>
                    <th className="pb-1.5 text-right font-medium">Tickets</th>
                    <th className="pb-1.5 text-right font-medium">Hit Rate</th>
                    <th className="pb-1.5 text-right font-medium">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {accaStats.by_market_combo.slice(0, 6).map((row, i) => (
                    <tr key={i} className="border-t border-[var(--border)]">
                      <td className="py-1.5 font-medium text-[var(--text-h)]">{row.markets}</td>
                      <td className="py-1.5 text-right text-[var(--text)]">{row.tickets}</td>
                      <td className={`py-1.5 text-right font-semibold ${row.hit_rate >= 20 ? 'text-green-400' : 'text-[var(--text-h)]'}`}>
                        {row.hit_rate?.toFixed(1)}%
                      </td>
                      <td className={`py-1.5 text-right font-semibold font-mono ${row.profit_loss >= 0 ? 'text-green-400' : 'text-red-400'}`} title={fmtPL(row.profit_loss)}>
                        {fmtPLCompact(row.profit_loss)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!isPro ? (
            <UpgradePrompt
              required="pro"
              feature="Build multi-leg accumulator tickets from the ranked board, including the full top 10 slip and a tighter best-5 option selected from it."
              onUpgrade={onUpgrade}
            />
          ) : (
            <AccumulatorBuilder recentBets={bets} onCreated={loadAccumulators} settings={settings} />
          )}

          {accumulators.length === 0 && (
            <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-8 flex flex-col items-center gap-2 text-center">
              <span className="text-3xl">🎰</span>
              <p className="text-sm font-semibold text-[var(--text-h)]">No saved tickets yet</p>
              <p className="text-xs text-[var(--text)] opacity-75 max-w-xs">
                Use the accumulator builder above to combine value signals into a ticket and save it here.
              </p>
            </div>
          )}

          {accumulators.length > 0 && (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5 space-y-5">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <h3 className="text-sm font-semibold text-[var(--text-h)]">
                  Saved Tickets ({accumulators.length})
                </h3>
                <div className="flex items-center gap-2">
                  {dedupResult != null && (
                    <span className={`text-xs font-medium ${dedupResult > 0 ? 'text-green-400' : 'text-[var(--text)]'}`}>
                      {dedupResult > 0 ? `✓ Removed ${dedupResult} duplicate${dedupResult === 1 ? '' : 's'}` : '✓ No duplicates found'}
                    </span>
                  )}
                  <button
                    onClick={handleDeduplicate}
                    disabled={deduping}
                    title="Remove duplicate tickets (keeps the oldest copy of each)"
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-red-500/30 text-red-400 text-xs hover:bg-red-500/10 disabled:opacity-50 transition-colors"
                  >
                    <Trash2 size={11} className={deduping ? 'animate-pulse' : ''} />
                    {deduping ? 'Cleaning…' : 'Remove Duplicates'}
                  </button>
                </div>
              </div>

              {groupedAccas.map(([dateK, list]) => {
                // Per-group totals — small, useful at-a-glance for "how much did
                // I have on this day"
                const totalStake = list.reduce((s, a) => s + (a.stake || 0), 0)
                const totalPL = list.reduce((s, a) => s + (a.profit_loss || 0), 0)
                const plColor = totalPL > 0 ? 'text-green-400' : totalPL < 0 ? 'text-red-400' : 'text-[var(--text)]'
                const tone = groupTone(dateK)

                return (
                  <section key={dateK} className="space-y-3">
                    {/* Tinted date band — colour encodes recency at a glance */}
                    <div className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 ${tone.band}`}>
                      <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide">
                        <span className={`inline-block h-2 w-2 rounded-full ${tone.dot}`} />
                        <span className={tone.label}>{formatDateHeading(dateK)}</span>
                        <span className="text-[var(--text)] opacity-70 normal-case font-normal">
                          · {list.length} ticket{list.length === 1 ? '' : 's'}
                        </span>
                      </h4>
                      <div className="text-[11px] text-[var(--text)] opacity-90 font-mono">
                        Stake <span className="text-[var(--text-h)]" title={fmtK(totalStake)}>{fmtKCompact(totalStake)}</span>
                        <span className="mx-2 opacity-70">·</span>
                        P/L <span className={`font-semibold ${plColor}`} title={fmtPL(totalPL)}>{fmtPLCompact(totalPL)}</span>
                      </div>
                    </div>

                    <div className="space-y-3">
                      {list.map(acca => <TicketCard key={acca.id} acca={acca} onDelete={handleDeleteAccumulator} />)}
                    </div>
                  </section>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Import CSV modal */}
      {showImport && (
        <ImportCSVModal
          onClose={() => setShowImport(false)}
          onImported={() => {
            loadBets(betFilters)
            setShowImport(false)
          }}
        />
      )}
    </div>
  )
}
