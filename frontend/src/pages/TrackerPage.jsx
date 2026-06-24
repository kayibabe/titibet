import React, { useState, useEffect, useMemo } from 'react'
import { RefreshCw, CheckCircle, TrendingUp, Lock, Upload, MoreHorizontal, Bot, User, Layers } from 'lucide-react'
import { useTracker } from '../store/useTracker'
import { syncData, computeCLV, deduplicateBets, normalizeStakes } from '../api/tracker'
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
const SOURCE_OPTIONS = [
  { value: '',       label: 'All Picks',    icon: null },
  { value: 'system', label: 'System Picks', icon: Bot  },
  { value: 'manual', label: 'Manual Picks', icon: User },
]

export default function TrackerPage({ user, settings, onUpgrade }) {
  const { isPro } = useTier()
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [syncing, setSyncing]           = useState(false)
  const [settling, setSettling]         = useState(false)
  const [settleResult, setSettleResult] = useState(null)
  const [computingCLV, setComputingCLV] = useState(false)
  const [showImport, setShowImport]     = useState(false)
  const [moreOpen, setMoreOpen]         = useState(false)
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

  const isSystemPick = b => b.source_rule_key === 'system_auto' || b.source_rule_key === 'system_dual'

  // Client-side source filter (system picks vs manual)
  const filteredBets = useMemo(() => {
    if (!sourceFilter) return bets
    if (sourceFilter === 'system') return bets.filter(isSystemPick)
    return bets.filter(b => !isSystemPick(b))
  }, [bets, sourceFilter])

  // System performance stats (all-time, shown in system-pick mode)
  const systemStats = useMemo(() => {
    const sys = bets.filter(isSystemPick)
    if (!sys.length) return null
    const won     = sys.filter(b => b.result_status === 'Won').length
    const lost    = sys.filter(b => b.result_status === 'Lost').length
    const settled = won + lost
    const totalStake  = sys.reduce((s, b) => s + (b.stake ?? 0), 0)
    const totalPL     = sys.filter(b => b.result_status !== 'Pending').reduce((s, b) => s + (b.profit_loss ?? 0), 0)
    return {
      total: sys.length,
      won, lost,
      settled,
      pending: sys.filter(b => b.result_status === 'Pending').length,
      hitRate: settled > 0 ? Math.round(won / settled * 100) : null,
      roi: totalStake > 0 ? Math.round(totalPL / totalStake * 1000) / 10 : null,
    }
  }, [bets])

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

  async function handleSync() {
    setSyncing(true)
    try { await syncData() } catch (e) { console.error(e) } finally { setSyncing(false) }
    invalidate()
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
              </span>
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

        {/* Normalize stakes result toast */}
        {normalizeResult != null && (
          <span className="text-xs font-medium text-emerald-400">
            ✓ {normalizeResult.updated} bet{normalizeResult.updated !== 1 ? 's' : ''} updated to K50,000
          </span>
        )}

        {/* Dedup result toast */}
        {dedupResult != null && (
          <span className="text-xs font-medium text-emerald-400">
            ✓ {dedupResult.removed} duplicate{dedupResult.removed !== 1 ? 's' : ''} removed
          </span>
        )}

        {/* Action error toast */}
        {actionError && (
          <span className="text-xs font-medium text-red-400">
            ✗ {actionError}
          </span>
        )}

        {/* CLV result toast */}
        {clvResult && (
          <span className="text-xs text-green-400 font-medium">
            ✓ {clvResult.updated} updated
            {clvResult.skipped_no_data > 0 && <span className="hidden sm:inline"> · {clvResult.skipped_no_data} no data</span>}
          </span>
        )}

        {/* Maintenance actions */}
        <div className="relative ml-auto">
          <button
            onClick={() => setMoreOpen(v => !v)}
            title="More actions"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-sm text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] transition-colors"
          >
            <MoreHorizontal size={15} />
            <span className="hidden sm:inline">More</span>
          </button>
          {moreOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMoreOpen(false)} />
              <div className="absolute right-0 mt-1 z-20 w-56 rounded-lg border border-[var(--border)] bg-[var(--bg)] shadow-xl p-1">
                {isPro ? (
                  <button
                    onClick={() => { setMoreOpen(false); handleComputeCLV() }}
                    disabled={computingCLV}
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-[var(--text-h)] hover:bg-[var(--code-bg)] disabled:opacity-50 transition-colors text-left"
                  >
                    <TrendingUp size={14} className={`text-green-400 ${computingCLV ? 'animate-pulse' : ''}`} />
                    {computingCLV ? 'Computing CLV…' : noCLVCount > 0 ? `Compute CLV (${noCLVCount})` : 'Refresh CLV'}
                  </button>
                ) : (
                  <div
                    title="Upgrade to Pro to compute Closing Line Value"
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-blue-400 cursor-default"
                  >
                    <Lock size={14} /> CLV · Pro
                  </div>
                )}
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

      {/* System performance card — visible when source=system or system bets exist */}
      {systemStats && (sourceFilter === 'system' || sourceFilter === '') && (
        <div className="rounded-xl border border-violet-500/25 bg-violet-500/6 px-4 py-3 space-y-2">
          <div className="flex items-center gap-2">
            <Bot size={13} className="text-violet-400 shrink-0" />
            <span className="text-xs font-semibold text-violet-300">System Performance</span>
            <span className="ml-auto text-xs text-[var(--text)] opacity-60">{systemStats.total} auto-tracked picks</span>
          </div>
          <div className="flex items-center gap-5 flex-wrap text-xs">
            <div className="flex flex-col items-center">
              <span className="text-lg font-bold text-[var(--text-h)] tabular-nums">
                {systemStats.hitRate !== null ? `${systemStats.hitRate}%` : '—'}
              </span>
              <span className="text-[var(--text)] opacity-60">Hit Rate</span>
            </div>
            <div className="flex flex-col items-center">
              <span className={`text-lg font-bold tabular-nums ${systemStats.roi === null ? 'text-[var(--text-h)]' : systemStats.roi >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {systemStats.roi !== null ? `${systemStats.roi >= 0 ? '+' : ''}${systemStats.roi}%` : '—'}
              </span>
              <span className="text-[var(--text)] opacity-60">ROI</span>
            </div>
            <div className="flex flex-col items-center">
              <span className="text-lg font-bold text-green-400 tabular-nums">{systemStats.won}</span>
              <span className="text-[var(--text)] opacity-60">Won</span>
            </div>
            <div className="flex flex-col items-center">
              <span className="text-lg font-bold text-red-400 tabular-nums">{systemStats.lost}</span>
              <span className="text-[var(--text)] opacity-60">Lost</span>
            </div>
            {systemStats.pending > 0 && (
              <div className="flex flex-col items-center">
                <span className="text-lg font-bold text-[var(--text-h)] tabular-nums">{systemStats.pending}</span>
                <span className="text-[var(--text)] opacity-60">Pending</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Filter bar */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3 space-y-3">
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
        {/* Source segmented control */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs text-[var(--text)] opacity-75 mr-1">Source:</span>
          {SOURCE_OPTIONS.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              onClick={() => setSourceFilter(value)}
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
          <BetStatsBar bets={filteredBets} />
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
      {!loading && <BetTable bets={filteredBets} isPro={isPro} onUpgrade={onUpgrade} onRefresh={() => { invalidate(); loadBets(betFilters) }} />}

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
