import React, { useState, useEffect, useMemo } from 'react'
import { RefreshCw, CheckCircle, TrendingUp, Lock, Upload, Settings2, Bot, User, Layers, ListChecks, ArrowRight, FileUp } from 'lucide-react'
import { useTracker } from '../store/useTracker'
import { syncData, computeCLV, deduplicateBets, normalizeStakes, fetchBets } from '../api/tracker'
import { fetchAnalytics } from '../api/analytics'
import { triggerAdminSettle } from '../api/admin'
import BetTable from '../components/tracker/BetTable'
import PLChart from '../components/tracker/PLChart'
import BetStatsBar from '../components/tracker/BetStatsBar'
import ImportCSVModal from '../components/tracker/ImportCSVModal'
import LoadingSpinner from '../components/shared/LoadingSpinner'
import DatePicker from '../components/shared/DatePicker'
import { fmtK } from '../utils/format'
import useTier from '../hooks/useTier'

const STATUS_OPTIONS = ['', 'Pending', 'Won', 'Lost', 'Void']

const ADVISORY_KEYS = ['scout_pick', 'strategist_pick', 'skeptic_pick']
const ADVISOR_META = {
  scout_pick:      { label: 'The Scout',      emoji: '🔭', color: 'blue'   },
  strategist_pick: { label: 'The Strategist', emoji: '♟️', color: 'violet' },
  skeptic_pick:    { label: 'The Skeptic',    emoji: '🧐', color: 'amber'  },
}

const SOURCE_OPTIONS = [
  { value: '',         label: 'All Picks',    icon: null },
  { value: 'system',   label: 'System Picks', icon: Bot  },
  { value: 'advisory', label: 'AI Advisory',  icon: Bot  },
  { value: 'manual',   label: 'Manual Picks', icon: User },
]

const DATE_PRESETS = [
  { label: 'Today',    fromOffset: 0,   toOffset: 0    },
  { label: 'Tomorrow', fromOffset: 1,   toOffset: 1    },
  { label: '7d',       fromOffset: -7,  toOffset: 0    },
  { label: '14d',      fromOffset: -14, toOffset: 0    },
  { label: '30d',      fromOffset: -30, toOffset: 0    },
  { label: 'All',      fromOffset: null, toOffset: null },
]

function toYMD(d) {
  return d.toISOString().slice(0, 10)
}

export default function TrackerPage({ user, settings, onUpgrade }) {
  const { isPro } = useTier()
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo]     = useState('')
  const [activePreset, setActivePreset] = useState('All')
  const [statusFilter, setStatusFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [advisorFilter, setAdvisorFilter] = useState('')
  const [syncing, setSyncing]           = useState(false)
  const [settling, setSettling]         = useState(false)
  const [settleResult, setSettleResult] = useState(null)
  const [computingCLV, setComputingCLV] = useState(false)
  const [showImport, setShowImport]     = useState(false)
  const [moreOpen, setMoreOpen]         = useState(false)
  const [refreshCount, setRefreshCount] = useState(0)
  const [allAdvisoryBets, setAllAdvisoryBets] = useState([])
  const [clvResult, setClvResult]       = useState(null)
  const [deduping, setDeduping]           = useState(false)
  const [dedupResult, setDedupResult]     = useState(null)
  const [normalizing, setNormalizing]     = useState(false)
  const [normalizeResult, setNormalizeResult] = useState(null)
  const [actionError, setActionError]     = useState(null)
  const { bets, loading, error, loadBets, invalidate } = useTracker()
  const [slowLoad, setSlowLoad] = useState(false)

  // After 8 s of loading with no data, surface a hint so the user isn't staring at a blank spinner
  useEffect(() => {
    if (!loading || bets.length > 0) { setSlowLoad(false); return }
    const id = setTimeout(() => setSlowLoad(true), 8_000)
    return () => clearTimeout(id)
  }, [loading, bets.length])

  const pendingCount = bets.filter(b => b.result_status === 'Pending').length
  const noCLVCount   = bets.filter(b => b.fixture_id && b.clv_pct == null).length

  const betFilters = { date_from: dateFrom || undefined, date_to: dateTo || undefined, result_status: statusFilter || undefined }

  const isAdvisoryPick = b => ADVISORY_KEYS.includes(b.source_rule_key)
  const isSystemPick   = b => !isAdvisoryPick(b) && b.market_type !== 'Accumulator'

  // Client-side source filter
  const filteredBets = useMemo(() => {
    if (sourceFilter === 'advisory') {
      const advisory = bets.filter(isAdvisoryPick)
      return advisorFilter ? advisory.filter(b => b.source_rule_key === advisorFilter) : advisory
    }
    if (sourceFilter === 'system') return bets.filter(isSystemPick)
    if (sourceFilter === 'manual') return bets.filter(b => !isSystemPick(b) && !isAdvisoryPick(b))
    return bets.filter(b => !isAdvisoryPick(b))
  }, [bets, sourceFilter, advisorFilter]) // eslint-disable-line

  // Analytics summary for the currently filtered view — same backend
  // build_analytics() implementation the Analytics page uses, scoped with
  // the same date/status/source filters as the bet list, so the stats bar
  // never drifts from a separately-implemented client-side formula.
  const [analyticsSummary, setAnalyticsSummary] = useState(null)
  useEffect(() => {
    fetchAnalytics({
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      result_status: statusFilter || undefined,
      source: sourceFilter || undefined,
    }).then(setAnalyticsSummary).catch(() => setAnalyticsSummary(null))
  }, [dateFrom, dateTo, statusFilter, sourceFilter, bets])

  // Performance banner — all-time, all picks, ignores the page's date/status filters.
  const [systemSummary, setSystemSummary] = useState(null)
  useEffect(() => {
    fetchAnalytics({}).then(setSystemSummary).catch(() => setSystemSummary(null))
  }, [bets])

  // Advisory performance — always all-time; refreshes after settle/sync, not on date filter changes.
  useEffect(() => {
    fetchBets({}).then(all => setAllAdvisoryBets(all.filter(isAdvisoryPick))).catch(() => {})
  }, [refreshCount]) // eslint-disable-line

  useEffect(() => {
    loadBets(betFilters)
  }, [dateFrom, dateTo, statusFilter, loadBets]) // eslint-disable-line

  // Auto-refresh when there are pending bets
  useEffect(() => {
    function handleVisibility() {
      if (document.visibilityState === 'visible' && pendingCount > 0) {
        loadBets(betFilters)
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [pendingCount]) // eslint-disable-line

  useEffect(() => {
    if (pendingCount === 0) return
    const id = setInterval(() => { loadBets(betFilters) }, 5 * 60 * 1000)
    return () => clearInterval(id)
  }, [pendingCount]) // eslint-disable-line

  function handlePreset(preset) {
    setActivePreset(preset.label)
    if (preset.fromOffset === null) {
      setDateFrom('')
      setDateTo('')
    } else {
      const today = new Date()
      const from  = new Date(today)
      from.setDate(today.getDate() + preset.fromOffset)
      const to = new Date(today)
      to.setDate(today.getDate() + preset.toOffset)
      setDateFrom(toYMD(from))
      setDateTo(toYMD(to))
    }
  }

  function handleDateFromChange(val) {
    setDateFrom(val)
    setActivePreset(null)
  }

  function handleDateToChange(val) {
    setDateTo(val)
    setActivePreset(null)
  }

  async function handleSync() {
    setSyncing(true)
    try { await syncData() } catch (e) { console.error(e) } finally { setSyncing(false) }
    invalidate()
    setRefreshCount(c => c + 1)
    await loadBets(betFilters)
  }

  async function handleSettle() {
    setSettling(true)
    setSettleResult(null)
    try {
      const res = await triggerAdminSettle()
      setSettleResult(res)
      setTimeout(() => setSettleResult(null), 6000)
    } catch (e) {
      console.error('Settle error:', e)
    } finally {
      setSettling(false)
    }
    invalidate()
    setRefreshCount(c => c + 1)
    await loadBets(betFilters)
  }

  async function handleNormalizeStakes() {
    setNormalizing(true)
    setNormalizeResult(null)
    try {
      const res = await normalizeStakes(50_000)
      setNormalizeResult(res)
      setTimeout(() => setNormalizeResult(null), 6000)
      invalidate()
      setRefreshCount(c => c + 1)
      await loadBets(betFilters)
    } catch (e) {
      setActionError(e.message || 'Failed — are you logged in?')
      setTimeout(() => setActionError(null), 7000)
    } finally { setNormalizing(false) }
  }

  async function handleDedup() {
    setDeduping(true)
    setDedupResult(null)
    try {
      const res = await deduplicateBets()
      setDedupResult(res)
      setTimeout(() => setDedupResult(null), 5000)
      invalidate()
      setRefreshCount(c => c + 1)
      await loadBets(betFilters)
    } catch (e) {
      setActionError(e.message || 'Failed — are you logged in?')
      setTimeout(() => setActionError(null), 7000)
    } finally { setDeduping(false) }
  }

  async function handleComputeCLV() {
    setComputingCLV(true)
    setClvResult(null)
    try {
      const result = await computeCLV()
      setClvResult(result)
      invalidate()
      await loadBets(betFilters)
    } catch (e) { console.error(e) }
    finally { setComputingCLV(false) }
  }

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

        {/* CLV — visible button, promoted from dropdown */}
        <div className="flex items-center gap-2 ml-auto">
          {isPro ? (
            <button
              onClick={handleComputeCLV}
              disabled={computingCLV}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-sm text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] disabled:opacity-50 transition-colors"
              title="Compute Closing Line Value for all settled bets"
            >
              <TrendingUp size={13} className={`text-green-400 ${computingCLV ? 'animate-pulse' : ''}`} />
              <span>{computingCLV ? 'Computing…' : noCLVCount > 0 ? `CLV (${noCLVCount})` : 'CLV'}</span>
            </button>
          ) : (
            <div
              title="Upgrade to Pro to compute Closing Line Value"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-blue-500/25 bg-blue-500/8 text-blue-400 text-sm select-none"
            >
              <Lock size={13} />
              <span>CLV</span>
            </div>
          )}

          {/* Maintenance dropdown — gear icon, low-frequency ops only */}
          <div className="relative">
            <button
              onClick={() => setMoreOpen(v => !v)}
              title="Maintenance actions"
              className="p-2 rounded-lg border border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] transition-colors"
            >
              <Settings2 size={15} />
            </button>
            {moreOpen && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setMoreOpen(false)} />
                <div className="absolute right-0 mt-1 z-20 w-52 rounded-lg border border-[var(--border)] bg-[var(--bg)] shadow-xl p-1">
                  <button
                    onClick={() => { setMoreOpen(false); handleNormalizeStakes() }}
                    disabled={normalizing}
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-[var(--text-h)] hover:bg-[var(--code-bg)] disabled:opacity-50 transition-colors text-left"
                    title="Set all bets to K50,000 flat stake and recompute P/L"
                  >
                    <Layers size={14} className={`text-emerald-400 ${normalizing ? 'animate-pulse' : ''}`} />
                    {normalizing ? 'Updating stakes…' : 'Set All Stakes → K50k'}
                  </button>
                  <button
                    onClick={() => { setMoreOpen(false); handleDedup() }}
                    disabled={deduping}
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-[var(--text-h)] hover:bg-[var(--code-bg)] disabled:opacity-50 transition-colors text-left"
                    title="Remove duplicate entries for the same fixture"
                  >
                    <Layers size={14} className={`text-amber-400 ${deduping ? 'animate-pulse' : ''}`} />
                    {deduping ? 'Removing duplicates…' : 'Remove Duplicates'}
                  </button>
                  <button
                    onClick={() => { setMoreOpen(false); setShowImport(true) }}
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-[var(--text-h)] hover:bg-[var(--code-bg)] transition-colors text-left"
                    title="Bulk-import historical bets from CSV"
                  >
                    <Upload size={14} /> Import CSV
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Action result status line — sits below the toolbar, never reflows buttons */}
      {(settleResult != null || normalizeResult != null || dedupResult != null || actionError || clvResult) && (() => {
        if (actionError) return (
          <p className="text-xs font-medium text-red-400 px-1">✗ {actionError}</p>
        )
        if (settleResult != null) {
          const r = settleResult
          const skipped = (r.skip_no_fixture ?? 0) + (r.skip_not_final ?? 0) + (r.skip_no_score ?? 0) + (r.skip_no_market ?? 0)
          return (
            <p className="text-xs font-medium text-emerald-400 px-1">
              ✓ {r.settled} settled{r.voided > 0 ? ` · ${r.voided} voided` : ''}
              {skipped > 0 && <span className="text-slate-400 ml-1">({skipped} skipped — not finished or missing data)</span>}
            </p>
          )
        }
        if (normalizeResult != null) return (
          <p className="text-xs font-medium text-emerald-400 px-1">✓ {normalizeResult.updated} bet{normalizeResult.updated !== 1 ? 's' : ''} updated to K50,000</p>
        )
        if (dedupResult != null) return (
          <p className="text-xs font-medium text-emerald-400 px-1">✓ {dedupResult.removed} duplicate{dedupResult.removed !== 1 ? 's' : ''} removed</p>
        )
        if (clvResult) return (
          <p className="text-xs font-medium text-green-400 px-1">✓ {clvResult.updated} CLV updated{clvResult.skipped_no_data > 0 ? ` · ${clvResult.skipped_no_data} no closing data` : ''}</p>
        )
        return null
      })()}

      {/* System performance card — visible when source=system or system bets exist */}
      {systemSummary && systemSummary.total_bets > 0 && (sourceFilter === 'system' || sourceFilter === '') && (
        <div className="rounded-xl border border-violet-500/25 bg-violet-500/6 px-4 py-3 space-y-2">
          <div className="flex items-center gap-2">
            <Bot size={13} className="text-violet-400 shrink-0" />
            <span className="text-xs font-semibold text-violet-300">System Performance</span>
            <span className="text-[10px] font-medium text-[var(--text)] opacity-50 bg-[var(--code-bg)] px-1.5 py-0.5 rounded">All-time</span>
            <span className="ml-auto text-xs text-[var(--text)] opacity-60">{systemSummary.total_bets} total picks</span>
          </div>
          <div className="flex items-center gap-5 flex-wrap text-xs">
            <div className="flex flex-col items-center">
              <span className="text-lg font-bold text-[var(--text-h)] tabular-nums">
                {systemSummary.settled_bets > 0 ? `${Math.round(systemSummary.win_rate)}%` : '—'}
              </span>
              <span className="text-[var(--text)] opacity-60">Hit Rate</span>
            </div>
            <div className="flex flex-col items-center">
              <span className={`text-lg font-bold tabular-nums ${systemSummary.settled_bets === 0 ? 'text-[var(--text-h)]' : systemSummary.roi >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {systemSummary.settled_bets > 0 ? `${systemSummary.roi >= 0 ? '+' : ''}${systemSummary.roi}%` : '—'}
              </span>
              <span className="text-[var(--text)] opacity-60">ROI</span>
            </div>
            <div className="flex flex-col items-center">
              <span className="text-lg font-bold text-green-400 tabular-nums">{systemSummary.wins}</span>
              <span className="text-[var(--text)] opacity-60">Won</span>
            </div>
            <div className="flex flex-col items-center">
              <span className="text-lg font-bold text-red-400 tabular-nums">{systemSummary.losses}</span>
              <span className="text-[var(--text)] opacity-60">Lost</span>
            </div>
            {systemSummary.pending_bets > 0 && (
              <div className="flex flex-col items-center">
                <span className="text-lg font-bold text-amber-400 tabular-nums">{systemSummary.pending_bets}</span>
                <span className="text-[var(--text)] opacity-60">Pending</span>
              </div>
            )}
            {(() => {
              const voidCount = systemSummary.total_bets - systemSummary.wins - systemSummary.losses - (systemSummary.pending_bets || 0)
              return voidCount > 0 ? (
                <div className="flex flex-col items-center">
                  <span className="text-lg font-bold text-slate-400 tabular-nums">{voidCount}</span>
                  <span className="text-[var(--text)] opacity-60">Void</span>
                </div>
              ) : null
            })()}
          </div>
        </div>
      )}

      {/* AI Advisory performance card — always all-time, unaffected by the date filter */}
      {(sourceFilter === 'advisory' || sourceFilter === '') && allAdvisoryBets.length > 0 && (
        <div className="rounded-xl border border-blue-500/25 bg-blue-500/5 px-4 py-3 space-y-3">
          <div className="flex items-center gap-2">
            <Bot size={13} className="text-blue-400 shrink-0" />
            <span className="text-xs font-semibold text-blue-300">AI Advisory Performance</span>
            <span className="text-[10px] font-medium text-[var(--text)] opacity-50 bg-[var(--code-bg)] px-1.5 py-0.5 rounded">All-time</span>
            <span className="ml-auto text-xs text-[var(--text)] opacity-60">{allAdvisoryBets.length} shadow picks</span>
          </div>
          <div className="grid grid-cols-3 gap-3">
            {ADVISORY_KEYS.map(key => {
              const meta    = ADVISOR_META[key]
              const picks   = allAdvisoryBets.filter(b => b.source_rule_key === key)
              const settled = picks.filter(b => b.result_status === 'Won' || b.result_status === 'Lost')
              const wins    = settled.filter(b => b.result_status === 'Won')
              const pending = picks.filter(b => b.result_status === 'Pending').length
              const hasEnough = settled.length >= 3
              const hitRate = hasEnough ? Math.round(wins.length / settled.length * 100) : null
              // ROI: theoretical flat-stake return across settled picks
              const roi = hasEnough
                ? Math.round(((wins.reduce((s, b) => s + (b.odds - 1), 0) - (settled.length - wins.length)) / settled.length) * 100)
                : null
              const colorMap = { blue: 'text-blue-400 border-blue-500/30', violet: 'text-violet-400 border-violet-500/30', amber: 'text-amber-400 border-amber-500/30' }
              const clr = colorMap[meta.color] || 'text-slate-400 border-slate-500/30'
              return (
                <button
                  key={key}
                  onClick={() => { setSourceFilter('advisory'); setAdvisorFilter(advisorFilter === key ? '' : key) }}
                  className={`rounded-lg border px-3 py-2.5 text-left transition-colors hover:bg-white/5 ${advisorFilter === key ? 'bg-white/8 ' + clr : 'border-[var(--border)]'}`}
                >
                  <div className="flex items-center gap-1.5 mb-2">
                    <span className="text-base leading-none">{meta.emoji}</span>
                    <span className={`text-xs font-semibold ${advisorFilter === key ? clr.split(' ')[0] : 'text-[var(--text-h)]'}`}>{meta.label}</span>
                  </div>
                  <div className="flex items-end gap-3 flex-wrap text-xs">
                    <div className="flex flex-col">
                      <span className={`text-lg font-bold tabular-nums ${hitRate === null ? 'text-[var(--text-h)]' : hitRate >= 55 ? 'text-green-400' : hitRate >= 45 ? 'text-amber-400' : 'text-red-400'}`}>
                        {hitRate !== null ? `${hitRate}%` : '—'}
                      </span>
                      <span className="text-[var(--text)] opacity-60">Hit Rate</span>
                    </div>
                    <div className="flex flex-col">
                      <span className={`text-lg font-bold tabular-nums ${roi === null ? 'text-[var(--text-h)]' : roi >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {roi !== null ? `${roi >= 0 ? '+' : ''}${roi}%` : '—'}
                      </span>
                      <span className="text-[var(--text)] opacity-60">ROI</span>
                    </div>
                    <div className="flex flex-col ml-auto text-right">
                      <span className="text-[var(--text-h)] font-semibold tabular-nums">{wins.length}W · {settled.length - wins.length}L{pending > 0 ? ` · ${pending}P` : ''}</span>
                      <span className="text-[var(--text)] opacity-60">{picks.length} picks</span>
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Filter bar */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <DatePicker label="From" value={dateFrom} onChange={handleDateFromChange} />
          <DatePicker label="To"   value={dateTo}   onChange={handleDateToChange} />
          {/* Quick-select presets */}
          <div className="flex items-center gap-1 pb-0.5">
            {DATE_PRESETS.map(preset => (
              <button
                key={preset.label}
                onClick={() => handlePreset(preset)}
                className={`px-2.5 py-1.5 rounded-md text-xs font-semibold border transition-colors ${
                  activePreset === preset.label
                    ? 'bg-[var(--accent)] text-white border-[var(--accent)]'
                    : 'border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--bg)]'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <label className="flex flex-col gap-1 text-sm text-[var(--text)]">
            <span className="font-medium">Status</span>
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
              className="w-full px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]">
              {STATUS_OPTIONS.map(o => <option key={o} value={o}>{o || 'All'}</option>)}
            </select>
          </label>
        </div>
        {/* Source segmented control */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs text-[var(--text)] opacity-75 mr-1">Source:</span>
          {SOURCE_OPTIONS.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              onClick={() => { setSourceFilter(value); if (value !== 'advisory') setAdvisorFilter('') }}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                sourceFilter === value
                  ? 'bg-[var(--accent)] text-white border-[var(--accent)]'
                  : 'border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--bg)]'
              }`}
            >
              {Icon && <Icon size={11} />}
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* P&L chart + stats bar */}
      {!loading && filteredBets.some(b => b.result_status !== 'Pending') && (
        <>
          <BetStatsBar summary={analyticsSummary} />
          <PLChart bets={filteredBets} />
        </>
      )}

      {loading && (
        <div className="flex flex-col items-center gap-3 py-8">
          <LoadingSpinner />
          {slowLoad && (
            <p className="text-xs text-[var(--text)] opacity-60 text-center max-w-xs">
              Server is starting up — this can take up to 30 seconds on first load.
            </p>
          )}
        </div>
      )}
      {error && (
        <div className="flex flex-col items-center gap-2 py-6">
          <p className="text-sm text-red-400">{error}</p>
          <button
            onClick={() => { invalidate(); loadBets(betFilters) }}
            className="text-xs px-3 py-1.5 rounded-lg border border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] transition-colors"
          >
            Retry
          </button>
        </div>
      )}
      {!loading && bets.length === 0 && !error ? (
        <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg)] p-10 flex flex-col items-center text-center gap-6">
          <div className="w-14 h-14 rounded-2xl bg-[var(--accent-bg)] border border-[var(--accent-border)] flex items-center justify-center">
            <ListChecks size={26} className="text-[var(--accent)]" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-[var(--text-h)] mb-2">No picks tracked yet</h2>
            <p className="text-sm text-[var(--text)] opacity-65 max-w-sm leading-relaxed">
              Track a signal and TiTiBet records the odds, monitors the result, and builds your P&amp;L history automatically.
            </p>
          </div>
          <ol className="flex flex-col sm:flex-row items-stretch gap-3 w-full max-w-sm">
            {[
              { step: '1', label: 'Open Signals', desc: 'Find today\'s ranked picks' },
              { step: '2', label: 'Click Track', desc: 'On any signal card' },
              { step: '3', label: 'Come back here', desc: 'Results update automatically' },
            ].map(({ step, label, desc }) => (
              <li key={step} className="flex-1 rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3 text-left list-none">
                <span className="text-[10px] font-bold text-[var(--accent)] opacity-70 uppercase tracking-widest">Step {step}</span>
                <p className="text-sm font-semibold text-[var(--text-h)] mt-0.5">{label}</p>
                <p className="text-xs text-[var(--text)] opacity-50 mt-0.5">{desc}</p>
              </li>
            ))}
          </ol>
          <div className="flex items-center gap-3 flex-wrap justify-center">
            <button
              onClick={() => window.dispatchEvent(new CustomEvent('titibet:navigate', { detail: 'signals' }))}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[var(--accent)] hover:opacity-90 text-white text-sm font-semibold transition-opacity"
            >
              Go to Signals
              <ArrowRight size={14} />
            </button>
            <button
              onClick={() => setShowImport(true)}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl border border-[var(--border)] hover:bg-[var(--code-bg)] text-[var(--text)] text-sm font-medium transition-colors"
            >
              <FileUp size={14} />
              Import CSV
            </button>
          </div>
          <p className="text-xs text-[var(--text)] opacity-35">
            Already have picks? Import a CSV from any spreadsheet.
          </p>
        </div>
      ) : (
        !loading && <BetTable bets={filteredBets} summary={analyticsSummary} isPro={isPro} onUpgrade={onUpgrade} onRefresh={() => { invalidate(); loadBets(betFilters) }} />
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
