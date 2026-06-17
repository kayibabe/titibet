import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { RefreshCw, Download, Calendar, Sparkles, TrendingUp, ArrowUpDown, SlidersHorizontal, AlertCircle, X, Filter, Target, Zap, HelpCircle } from 'lucide-react'
import { useSignals } from '../store/useSignals'
import { computeSignals, fetchSignals } from '../api/signals'
import { syncData, fetchBets, autoTrackSignal } from '../api/tracker'
import SignalCard from '../components/signals/SignalCard'
import ValueBetCard, { adjustOdd, ODDS_TIERS } from '../components/signals/ValueBetCard'
import TrackModal from '../components/tracker/TrackModal'
import LoadingSpinner from '../components/shared/LoadingSpinner'
import AIAdvisorPanel from '../components/signals/AIAdvisorPanel'
import UpgradePrompt from '../components/shared/UpgradePrompt'
import useTier from '../hooks/useTier'
import { useSettings } from '../store/useSettings'
import { useAuth } from '../context/AuthContext'
import { useOnboarding } from '../hooks/useOnboarding'
import OnboardingModal from '../components/shared/OnboardingModal'

const FREE_SIGNAL_LIMIT = 5

const CONFIDENCE_OPTIONS = ['', 'High', 'Medium', 'Low']
const AGREEMENT_OPTIONS  = ['', 'Both', 'Bayesian Only', 'Poisson Only', 'Contradiction']
const MARKET_FAMILY_OPTIONS = [
  '',
  'Goals',
  'BTTS',
  'Safer Cover',
  'Team Totals',
  'Clean Sheet',
  'Exact Goals',
]
const MARKET_OPTIONS     = [
  '',
  // Full-game totals
  'Over 0.5', 'Over 1.5', 'Over 2.5', 'Over 3.5',
  'Under 1.5', 'Under 2.5', 'Under 3.5',
  // BTTS
  'BTTS Yes', 'BTTS No',
  // Double chance
  '1X (Home or Draw)', 'X2 (Draw or Away)', '12 (Home or Away)',
  // Team totals
  'Home Over 0.5', 'Home Under 0.5', 'Home Over 1.5', 'Home Under 1.5',
  'Away Over 0.5', 'Away Under 0.5', 'Away Over 1.5', 'Away Under 1.5',
  // Win to nil
  'Home Win to Nil', 'Away Win to Nil',
  // Exact goals
  'Exactly 1 Goal', 'Exactly 2 Goals', 'Exactly 3 Goals',
]

const SORT_OPTIONS = [
  { value: 'system',      label: 'System Rank' },
  { value: 'quality',     label: 'Quality' },
  { value: 'ev',          label: '+EV %' },
  { value: 'probability', label: 'Prob %' },
  { value: 'kickoff',     label: 'Kickoff' },
  { value: 'stake',       label: 'Stake %' },
]

function getMarketFamily(market) {
  if (!market) return 'Other'
  if (
    market === 'Over 0.5' ||
    market === 'Over 1.5' ||
    market === 'Over 2.5' ||
    market === 'Over 3.5' ||
    market === 'Under 1.5' ||
    market === 'Under 2.5' ||
    market === 'Under 3.5'
  ) return 'Goals'
  if (market === 'BTTS Yes' || market === 'BTTS No') return 'BTTS'
  if (
    market === '1X (Home or Draw)' ||
    market === 'X2 (Draw or Away)' ||
    market === '12 (Home or Away)'
  ) return 'Safer Cover'
  if (
    market === 'Home Over 0.5' || market === 'Home Under 0.5' ||
    market === 'Home Over 1.5' || market === 'Home Under 1.5' ||
    market === 'Away Over 0.5' || market === 'Away Under 0.5' ||
    market === 'Away Over 1.5' || market === 'Away Under 1.5'
  ) return 'Team Totals'
  if (market === 'Home Win to Nil' || market === 'Away Win to Nil') return 'Clean Sheet'
  if (
    market === 'Exactly 1 Goal' ||
    market === 'Exactly 2 Goals' ||
    market === 'Exactly 3 Goals'
  ) return 'Exact Goals'
  return 'Other'
}

function fmtDate(iso) {
  const d = new Date(iso + 'T00:00:00')
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
}

function FilterSelect({ label, value, onChange, options, tooltip }) {
  return (
    <label className="flex flex-col gap-1 text-sm text-[var(--text)]">
      <span className="font-medium opacity-85 flex items-center gap-1">
        {label}
        {tooltip && (
          <span title={tooltip}>
            <HelpCircle size={11} className="text-slate-500 cursor-help" />
          </span>
        )}
      </span>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]"
      >
        {options.map(o => <option key={o} value={o}>{o || 'All'}</option>)}
      </select>
    </label>
  )
}

// Sort pill button
function SortPill({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
        active
          ? 'bg-[var(--accent)] text-white border-[var(--accent)]'
          : 'border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'
      }`}
    >
      {label}
    </button>
  )
}

const TABS = [
  { id: 'signals',   label: 'Signals',     icon: TrendingUp },
  { id: 'valuebets', label: 'Value Bets',  icon: Zap        },
  { id: 'advisor',   label: 'AI Advisory', icon: Sparkles   },
]

const VB_FREE_LIMIT = 5

function ValueBetsTab({ date, isPro, onUpgrade, oddsAdjPct = 0 }) {
  const [minOdds, setMinOdds] = useState(1.5)
  const [allSignals, setAllSignals] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchSignals({ date, sort_by: 'system' })
      .then(data => setAllSignals(Array.isArray(data) ? data : []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [date])

  const valueBets = useMemo(() => {
    return allSignals
      .filter(s => {
        const adj = adjustOdd(s.bayesian?.best_odd, oddsAdjPct)
        return s.bayesian?.is_value && (adj ?? 0) >= minOdds
      })
      .sort((a, b) => {
        const evA = oddsAdjPct && a.bayesian?.prob != null ? (a.bayesian.prob * (adjustOdd(a.bayesian.best_odd, oddsAdjPct) ?? 0) - 1) * 100 : (a.bayesian?.ev_pct ?? 0)
        const evB = oddsAdjPct && b.bayesian?.prob != null ? (b.bayesian.prob * (adjustOdd(b.bayesian.best_odd, oddsAdjPct) ?? 0) - 1) * 100 : (b.bayesian?.ev_pct ?? 0)
        return evB - evA
      })
  }, [allSignals, minOdds, oddsAdjPct])

  const displayed   = isPro ? valueBets : valueBets.slice(0, VB_FREE_LIMIT)
  const lockedCount = valueBets.length - displayed.length
  const avgEv = valueBets.length
    ? (valueBets.reduce((s, x) => {
        const adj = adjustOdd(x.bayesian?.best_odd, oddsAdjPct)
        const ev = oddsAdjPct && x.bayesian?.prob != null && adj != null ? (x.bayesian.prob * adj - 1) * 100 : (x.bayesian?.ev_pct ?? 0)
        return s + ev
      }, 0) / valueBets.length).toFixed(1)
    : null

  return (
    <div className="space-y-5">
      <p className="text-xs text-[var(--text)] opacity-70">
        Bets where our dual-engine probability beats the bookmaker's implied probability — positive expected value identified by the Bayesian engine.
      </p>

      {/* Odds tier picker */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-[var(--text)] opacity-80 shrink-0">Min odds:</span>
        {ODDS_TIERS.map(tier => {
          const selected = minOdds === tier.min
          const count = allSignals.filter(s => {
            const adj = adjustOdd(s.bayesian?.best_odd, oddsAdjPct)
            return s.bayesian?.is_value && (adj ?? 0) >= tier.min
          }).length
          return (
            <button key={tier.min} onClick={() => setMinOdds(tier.min)} title={tier.desc}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-semibold transition-colors ${selected ? 'border-[var(--accent)] bg-[var(--accent-bg)] text-[var(--accent)]' : 'border-[var(--border)] text-[var(--text)] hover:border-[var(--accent)]/50 hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'}`}>
              {tier.label}
              {!loading && count > 0 && <span className={`text-[10px] font-bold tabular-nums ${selected ? 'opacity-80' : 'text-emerald-400'}`}>{count}</span>}
            </button>
          )
        })}
      </div>

      {!loading && !error && valueBets.length > 0 && (
        <div className="flex items-center gap-4 text-xs text-[var(--text)] opacity-70">
          <span><span className="font-semibold text-[var(--text-h)]">{valueBets.length}</span> value bet{valueBets.length !== 1 ? 's' : ''}</span>
          {avgEv && <span>Avg EV: <span className="font-semibold text-emerald-400">+{avgEv}%</span></span>}
          <span className="ml-auto">Sorted by highest EV</span>
        </div>
      )}

      {error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">{error}</div>}

      {loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 animate-pulse">
          {[1,2,3,4,5,6].map(i => <div key={i} className="h-40 rounded-xl bg-[var(--border)]" />)}
        </div>
      )}

      {!loading && !error && valueBets.length === 0 && (
        <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-12 flex flex-col items-center gap-3 text-center">
          <span className="text-4xl">🔍</span>
          <p className="text-sm font-semibold text-[var(--text-h)]">No value bets at {minOdds}+ odds</p>
          <p className="text-xs text-[var(--text)] opacity-80 max-w-xs">Try lowering the minimum odds, or check a different date.</p>
        </div>
      )}

      {!loading && displayed.length > 0 && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
            {displayed.map((signal, i) => <ValueBetCard key={signal.id} signal={signal} rank={i + 1} oddsAdjPct={oddsAdjPct} />)}
          </div>
          {lockedCount > 0 && (
            <div className="relative">
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 opacity-30 pointer-events-none select-none">
                {valueBets.slice(VB_FREE_LIMIT, VB_FREE_LIMIT + 3).map(signal => <ValueBetCard key={signal.id} signal={signal} oddsAdjPct={oddsAdjPct} />)}
              </div>
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="rounded-xl bg-[var(--bg)] border border-[var(--border)] shadow-lg px-6 py-4 text-center space-y-2">
                  <Zap size={18} className="mx-auto text-[var(--accent)]" />
                  <p className="text-sm font-semibold text-[var(--text-h)]">{lockedCount} more value bet{lockedCount !== 1 ? 's' : ''} locked</p>
                  <button onClick={onUpgrade} className="text-xs font-semibold text-[var(--accent)] hover:underline">Upgrade to Pro →</button>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default function SignalsPage({ settings, onDeepDive, onUpgrade, onNavigateToTracker, initialFilter, onFilterConsumed }) {
  const { isPro } = useTier()
  const { user } = useAuth()
  const { showOnboarding, completeOnboarding } = useOnboarding(user)
  const { settings: storeSettings } = useSettings()
  const oddsAdjPct = settings?.oddsAdjustmentPct ?? storeSettings?.oddsAdjustmentPct ?? 0
  const today = (() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
  })()

  const [date, setDate]             = useState(today)
  const [activeTab, setActiveTab]   = useState('signals')
  const [confidence, setConfidence] = useState(() => settings?.defaultConfidence || '')
  const [agreement, setAgreement]   = useState(() => settings?.defaultAgreement  || '')
  const [marketFamily, setMarketFamily] = useState('')
  const [market, setMarket]         = useState('')
  const [sortBy, setSortBy]         = useState('system')
  const [bestPerFixture, setBestPerFixture] = useState(true)
  const [minEv, setMinEv]           = useState('')
  const [minProb, setMinProb]       = useState('')
  const [leagueSearch, setLeagueSearch] = useState('')
  const [showSavedOnly, setShowSavedOnly] = useState(false)
  const [filtersOpen, setFiltersOpen]   = useState(false)

  const getSavedIds = () => { try { return JSON.parse(localStorage.getItem('titibet_saved_signals_v1') || '[]') } catch { return [] } }
  const [syncing, setSyncing]       = useState(false)
  const [computing, setComputing]   = useState(false)
  const [trackingSignal, setTrackingSignal] = useState(null)
  const [trackedToast, setTrackedToast]     = useState(null) // { market, match } | null
  // Tracks which filters came from Analytics so we can show the "from Analytics" banner
  const [analyticsFilter, setAnalyticsFilter] = useState(null) // { label, fields } | null
  // Set of "fixture_id:market" keys for picks already in the tracker
  const [trackedKeys, setTrackedKeys] = useState(new Set())

  // Load today's bets once so we can badge already-tracked signals and show system stats
  const [systemStats, setSystemStats] = useState(null) // { total, won, lost, pending }
  useEffect(() => {
    fetchBets({ date_from: today, date_to: today })
      .then(bets => {
        setTrackedKeys(new Set(bets.map(b => `${b.fixture_id}:${b.market_type}`)))
        const sys = bets.filter(b => b.source_rule_key === 'system_auto' || b.source_rule_key === 'system_dual')
        if (sys.length > 0) {
          const won     = sys.filter(b => b.result_status === 'Won').length
          const lost    = sys.filter(b => b.result_status === 'Lost').length
          const pending = sys.filter(b => b.result_status === 'Pending').length
          setSystemStats({ total: sys.length, won, lost, pending })
        }
      })
      .catch(() => {})
  }, []) // eslint-disable-line

  // Consume initialFilter from Analytics page — apply it once, then clear
  useEffect(() => {
    if (!initialFilter) return
    if (initialFilter.market)     setMarket(initialFilter.market)
    if (initialFilter.confidence) setConfidence(initialFilter.confidence)
    if (initialFilter.agreement)  setAgreement(initialFilter.agreement)
    setActiveTab('signals')
    setAnalyticsFilter(initialFilter)
    onFilterConsumed?.()
  }, [initialFilter]) // eslint-disable-line

  function clearAnalyticsFilter() {
    setMarket('')
    setConfidence('')
    setAgreement('')
    setAnalyticsFilter(null)
  }

  function clearAllFilters() {
    setConfidence('')
    setAgreement('')
    setMarketFamily('')
    setMarket('')
    setMinEv('')
    setMinProb('')
    setLeagueSearch('')
    setShowSavedOnly(false)
    setAnalyticsFilter(null)
  }

  // Count how many filters are active (for badge)
  const activeFilterCount = [
    confidence, agreement, marketFamily, market,
    minEv !== '' ? minEv : '',
    minProb !== '' ? minProb : '',
    leagueSearch,
  ].filter(Boolean).length
const dateInputRef = useRef(null)
  const { signals, loading, error, load } = useSignals()
const params = {
    date,
    confidence:  confidence || undefined,
    agreement:   agreement  || undefined,
    market:      market     || undefined,
    sort_by:     sortBy,
    min_quality: (settings?.minQuality > 0) ? settings.minQuality : undefined,
    best_per_fixture: bestPerFixture,
  }

  useEffect(() => { load(params) }, [date, confidence, agreement, market, sortBy, settings?.minQuality, bestPerFixture]) // eslint-disable-line
const reload = () => load(params)

  const isToday = date === today
  const isBusy  = syncing || computing

  // Auto-track ref: prevents re-issuing calls for the same signal in one session
  const autoTrackedRef = useRef(new Set())
  const [autoTrackedKeys, setAutoTrackedKeys] = useState(new Set())

  // When today's signals load, auto-track each one as a system pick (fire-and-forget)
  useEffect(() => {
    if (!isToday || loading || error || !signals.length) return
    const bankroll = settings?.bankroll || 1000
    const newKeys = []
    signals.forEach(signal => {
      const key = `${signal.fixture_id}:${signal.market}`
      if (autoTrackedRef.current.has(key)) return
      autoTrackedRef.current.add(key)
      newKeys.push(key)
      autoTrackSignal(signal, { bankroll }).catch(() => {})
    })
    if (newKeys.length) setAutoTrackedKeys(prev => new Set([...prev, ...newKeys]))
  }, [signals, isToday, loading, error]) // eslint-disable-line

  // ── Client-side sort + EV filter ────────────────────────────────────────
  const displayedSignals = useMemo(() => {
    let list = [...signals]

    if (settings?.hideContradictions) {
      list = list.filter(s => s.dual_agreement !== 'Contradiction')
    }

    if (marketFamily) {
      list = list.filter(s => getMarketFamily(s.market) === marketFamily)
    }

    // League / country text search
    if (leagueSearch.trim()) {
      const q = leagueSearch.trim().toLowerCase()
      list = list.filter(s =>
        (s.league  || '').toLowerCase().includes(q) ||
        (s.country || '').toLowerCase().includes(q)
      )
    }

    // Min EV% filter
    const evThreshold = minEv !== '' ? parseFloat(minEv) : null
    if (evThreshold !== null && !isNaN(evThreshold)) {
      list = list.filter(s => (s.bayesian?.ev_pct ?? -Infinity) >= evThreshold)
    }

    // Min probability filter
    const probThreshold = minProb !== '' ? parseFloat(minProb) / 100 : null
    if (probThreshold !== null && !isNaN(probThreshold)) {
      list = list.filter(s => {
        const primary = Math.max(s.bayesian?.prob ?? 0, s.poisson?.prob ?? 0)
        return primary >= probThreshold
      })
    }

    // Sort
    switch (sortBy) {
      case 'system':
        // The API already returns one best signal per fixture in authoritative system-rank order.
        break
      case 'ev':
        list.sort((a, b) => (b.bayesian?.ev_pct ?? -Infinity) - (a.bayesian?.ev_pct ?? -Infinity))
        break
      case 'probability':
        list.sort((a, b) => (b.bayesian?.prob ?? -Infinity) - (a.bayesian?.prob ?? -Infinity))
        break
      case 'kickoff': {
        const toUtc = v => v ? new Date(v.endsWith('Z') || v.includes('+') ? v : v + 'Z') : new Date(0)
        list.sort((a, b) => toUtc(a.kickoff_at) - toUtc(b.kickoff_at))
      }
        break
      case 'stake':
        list.sort((a, b) => (b.dual_recommended_stake_pct ?? 0) - (a.dual_recommended_stake_pct ?? 0))
        break
      case 'quality':
        list.sort((a, b) => (b.dual_quality_score ?? -Infinity) - (a.dual_quality_score ?? -Infinity))
        break
      default:
        break
    }

    if (showSavedOnly) {
      const savedIds = getSavedIds()
      list = list.filter(s => savedIds.includes(s.id))
    }

    return list
  }, [signals, marketFamily, sortBy, minEv, minProb, leagueSearch, showSavedOnly]) // eslint-disable-line

  // Summary stats for the result bar
  const stats = useMemo(() => {
    if (!displayedSignals.length) return null
    const evValues = displayedSignals.map(s => s.bayesian?.ev_pct).filter(v => v != null)
    const avgEv = evValues.length ? evValues.reduce((a, b) => a + b, 0) / evValues.length : null
    const highConf = displayedSignals.filter(s => s.dual_confidence === 'High').length
    return { total: displayedSignals.length, avgEv, highConf }
  }, [displayedSignals])

  const _LIVE_SET = new Set(['1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE', 'INT'])
  const hasLiveMatches = signals.some(s => _LIVE_SET.has((s.status || '').trim().toUpperCase()))

  async function handleSync() {
    setSyncing(true)
    try {
      await syncData(date, { force: true })
      await computeSignals(date)
      await reload()
    } catch (e) { console.error(e) }
    finally { setSyncing(false) }
  }

  async function handleRecompute() {
    setComputing(true)
    try {
      await computeSignals(date)
      await reload()
    } catch (e) { console.error(e) }
    finally { setComputing(false) }
  }

  // Auto-refresh every 5 min when LIVE matches are on screen (today only)
  const silentReload = useCallback(async () => {
    try { await reload() } catch { /* silent */ }
  }, [reload]) // eslint-disable-line

  useEffect(() => {
    if (!isToday || !hasLiveMatches) return
    const id = setInterval(silentReload, 5 * 60 * 1000)
    return () => clearInterval(id)
  }, [isToday, hasLiveMatches, silentReload])

  function trackingSourceFamily() {
    if (sortBy === 'system') return 'Signals Board'
    if (sortBy === 'quality') return 'Quality View'
    if (sortBy === 'ev') return 'EV View'
    if (sortBy === 'probability') return 'Probability View'
    if (sortBy === 'stake') return 'Stake View'
    return 'Signals Board'
  }

  return (
    <div className="space-y-5">

      {showOnboarding && <OnboardingModal onComplete={completeOnboarding} />}

      {/* ── Toolbar ───────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 flex-wrap">
        <div
          className="relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] cursor-pointer group hover:bg-[var(--code-bg)] transition-colors"
          onClick={() => !isBusy && dateInputRef.current?.showPicker()}
          title="Pick a date"
        >
          <Calendar size={13} className="text-[var(--accent)] shrink-0" />
          <span className="text-sm font-medium text-[var(--text-h)] group-hover:text-[var(--accent)] transition-colors select-none">
            {fmtDate(date)}{isToday ? ' · Today' : ''}
          </span>
          <input
            ref={dateInputRef}
            type="date"
            value={date}
            max={today}
            onChange={e => e.target.value && setDate(e.target.value)}
            disabled={isBusy}
            style={{ position: 'absolute', opacity: 0, pointerEvents: 'none', width: 0, height: 0, bottom: 0, left: 0 }}
          />
        </div>

        <button
          onClick={handleRecompute}
          disabled={isBusy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-sm text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] disabled:opacity-50 transition-colors"
          title="Re-run engines on cached odds — no API call"
        >
          <RefreshCw size={13} className={computing ? 'animate-spin' : ''} />
          <span className="hidden sm:inline">{computing ? 'Computing…' : 'Recompute'}</span>
          <span className="sm:hidden">{computing ? '…' : 'Run'}</span>
        </button>

        <button
          onClick={handleSync}
          disabled={isBusy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
          title="Pull fresh odds from API-Football for this date, then recompute"
        >
          <Download size={13} className={syncing ? 'animate-bounce' : ''} />
          <span className="hidden sm:inline">{syncing ? 'Syncing…' : 'Sync API'}</span>
          <span className="sm:hidden">{syncing ? '…' : 'Sync'}</span>
        </button>
      </div>

      {/* ── Tab bar ───────────────────────────────────────────────────────── */}
      <div className="flex gap-1 border-b border-[var(--border)]">
        {TABS.map(({ id, label, icon: Icon }) => {
          const locked = id === 'advisor' && !isPro
          return (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`
                flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
                ${activeTab === id
                  ? 'border-[var(--accent)] text-[var(--accent)]'
                  : 'border-transparent text-[var(--text)] hover:text-[var(--text-h)]'
                }
              `}
            >
              <Icon size={13} />
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

      {/* ── SIGNALS TAB ───────────────────────────────────────────────────── */}
      <div className={activeTab === 'signals' ? 'space-y-4' : 'hidden'}>

        {/* ── System performance bar ────────────────────────────────────── */}
        {isToday && (systemStats || autoTrackedKeys.size > 0) && (() => {
          const tracked = systemStats?.total ?? autoTrackedKeys.size
          const won     = systemStats?.won ?? 0
          const lost    = systemStats?.lost ?? 0
          const settled = won + lost
          const hitRate = settled > 0 ? Math.round(won / settled * 100) : null
          return (
            <div className="flex items-center gap-3 px-4 py-2.5 rounded-xl border border-[var(--accent)]/20 bg-[var(--accent)]/6 text-xs flex-wrap">
              <span className="flex items-center gap-1.5 text-[var(--accent)] font-semibold shrink-0">
                <Zap size={11} />
                System Auto-Tracking
              </span>
              <span className="text-[var(--text)] opacity-80">
                <span className="font-semibold text-[var(--text-h)]">{tracked}</span> pick{tracked !== 1 ? 's' : ''} tracked today
              </span>
              {settled > 0 && (
                <>
                  <span className="text-[var(--text)] opacity-40">·</span>
                  <span className="text-[var(--text)] opacity-80">
                    <span className="font-semibold text-green-400">{won}W</span>{' / '}
                    <span className="font-semibold text-red-400">{lost}L</span>
                    {hitRate !== null && (
                      <span className="ml-1 font-semibold text-[var(--text-h)]">({hitRate}%)</span>
                    )}
                  </span>
                </>
              )}
              {systemStats?.pending > 0 && (
                <>
                  <span className="text-[var(--text)] opacity-40">·</span>
                  <span className="text-[var(--text)] opacity-60">{systemStats.pending} pending</span>
                </>
              )}
              <button
                onClick={onNavigateToTracker}
                className="ml-auto text-[var(--accent)] font-semibold hover:underline shrink-0"
              >
                View in Tracker →
              </button>
            </div>
          )
        })()}

        {/* ── Analytics filter banner ───────────────────────────────────── */}
        {analyticsFilter && (
          <div className="flex items-center gap-3 rounded-lg border border-[var(--accent)]/30 bg-[var(--accent)]/8 px-4 py-2.5 text-sm">
            <span className="text-[var(--accent)] font-semibold shrink-0">From Analytics</span>
            <span className="flex-1 text-[var(--text)] opacity-85 text-xs">
              {analyticsFilter.label}
            </span>
            <button
              onClick={clearAnalyticsFilter}
              className="shrink-0 flex items-center gap-1 text-xs text-[var(--text)] opacity-65 hover:opacity-100 hover:text-[var(--text-h)] transition-colors"
              title="Clear this filter"
            >
              <X size={12} /> Clear filter
            </button>
          </div>
        )}

        {/* ── Filter + Sort bar ─────────────────────────────────────────── */}
        <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] overflow-hidden">

          {/* Collapsible header */}
          <button
            onClick={() => setFiltersOpen(v => !v)}
            className="w-full flex items-center gap-2 px-4 py-2.5 hover:bg-[var(--bg)] transition-colors text-left"
          >
            <Filter size={12} className="text-[var(--accent)] shrink-0" />
            <span className="text-xs font-semibold text-[var(--text-h)]">Filters & Sort</span>
            {activeFilterCount > 0 && (
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-[var(--accent)] text-white">
                {activeFilterCount}
              </span>
            )}
            {activeFilterCount > 0 && (
              <button
                onClick={e => { e.stopPropagation(); clearAllFilters() }}
                className="ml-auto text-[10px] text-[var(--text)] opacity-80 hover:opacity-100 hover:text-red-400 flex items-center gap-1 transition-colors"
              >
                <X size={10} /> Reset all
              </button>
            )}
            {activeFilterCount === 0 && (
              <span className="ml-auto text-[10px] text-[var(--text)] opacity-65">
                {filtersOpen ? '▲' : '▼'}
              </span>
            )}
          </button>

          {filtersOpen && (
            <div className="px-4 pb-3 pt-1 space-y-3 border-t border-[var(--border)]">

              {/* Row 1: dropdowns */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <FilterSelect label="Confidence"    value={confidence}   onChange={setConfidence}   options={CONFIDENCE_OPTIONS} />
                <FilterSelect label="Agreement"     value={agreement}    onChange={setAgreement}    options={AGREEMENT_OPTIONS} />
                <FilterSelect label="Market Family" value={marketFamily} onChange={setMarketFamily} options={MARKET_FAMILY_OPTIONS} tooltip="Betting market category: Goals (Over/Under), BTTS, Safer Cover (double chance), Team Totals, Clean Sheet, or Exact Goals" />
                <FilterSelect label="Market"        value={market}       onChange={setMarket}       options={MARKET_OPTIONS} />
              </div>

              {/* Row 2: numeric + text inputs */}
              <div className="flex flex-wrap gap-3">
                {/* League search */}
                <label className="flex flex-col gap-1 text-sm text-[var(--text)] flex-1 min-w-[140px]">
                  <span className="font-medium opacity-85 text-xs">League / Country</span>
                  <div className="relative">
                    <input
                      type="text"
                      placeholder="e.g. Premier League"
                      value={leagueSearch}
                      onChange={e => setLeagueSearch(e.target.value)}
                      className="w-full pl-3 pr-6 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]"
                    />
                    {leagueSearch && (
                      <button onClick={() => setLeagueSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--text)] opacity-65 hover:opacity-100 text-xs">✕</button>
                    )}
                  </div>
                </label>

                {/* Min EV% */}
                <label className="flex flex-col gap-1 text-sm text-[var(--text)] w-28">
                  <span className="font-medium opacity-85 text-xs flex items-center gap-1">
                    <SlidersHorizontal size={10} /> Min EV %
                  </span>
                  <div className="relative">
                    <input
                      type="number"
                      placeholder="e.g. 5"
                      value={minEv}
                      onChange={e => setMinEv(e.target.value)}
                      className="w-full pl-3 pr-6 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                    />
                    {minEv && (
                      <button onClick={() => setMinEv('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--text)] opacity-65 hover:opacity-100 text-xs">✕</button>
                    )}
                  </div>
                </label>

                {/* Min Prob% */}
                <label className="flex flex-col gap-1 text-sm text-[var(--text)] w-28">
                  <span className="font-medium opacity-85 text-xs flex items-center gap-1">
                    <SlidersHorizontal size={10} /> Min Prob %
                  </span>
                  <div className="relative">
                    <input
                      type="number"
                      placeholder="e.g. 60"
                      min="0"
                      max="100"
                      value={minProb}
                      onChange={e => setMinProb(e.target.value)}
                      className="w-full pl-3 pr-6 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                    />
                    {minProb && (
                      <button onClick={() => setMinProb('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--text)] opacity-65 hover:opacity-100 text-xs">✕</button>
                    )}
                  </div>
                </label>
              </div>

              {/* Saved only toggle */}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowSavedOnly(v => !v)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                    showSavedOnly
                      ? 'bg-red-500/20 border-red-400/50 text-red-300'
                      : 'border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'
                  }`}
                >
                  ♥ Saved only
                </button>
              </div>

              {/* Row 3: Sort pills */}
              <div className="flex items-center gap-2 flex-wrap pt-0.5">
                <span className="text-xs font-medium text-[var(--text)] opacity-85 flex items-center gap-1">
                  <ArrowUpDown size={11} /> Sort
                </span>
                {SORT_OPTIONS.map(opt => (
                  <SortPill
                    key={opt.value}
                    label={opt.label}
                    active={sortBy === opt.value}
                    onClick={() => setSortBy(opt.value)}
                  />
                ))}
              </div>

              {/* Card-border legend — reference material, kept out of the board */}
              <div className="flex flex-wrap items-center gap-3 text-xs text-[var(--text)] opacity-75 pt-1 border-t border-[var(--border)]">
                <span className="font-medium">Card borders:</span>
                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-emerald-500/60 border border-emerald-400"></span> High probability (70%+)</span>
                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-amber-500/60 border border-amber-400"></span> Medium confidence</span>
                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-red-500/60 border border-red-400"></span> Contradiction</span>
              </div>
            </div>
          )}
        </div>

        {/* ── Results-pending banner ───────────────────────────────────── */}
        {!loading && hasLiveMatches && (
          <div className="flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/8 px-4 py-2.5 text-sm">
            <AlertCircle size={14} className="text-amber-400 shrink-0" />
            <p className="flex-1 text-[var(--text)] opacity-85">
              {isToday
                ? <>Some matches are <span className="font-semibold text-amber-400">still in progress or recently finished</span> — sync to fetch the latest scores.</>
                : <>Results for this date <span className="font-semibold text-amber-400">weren&apos;t captured</span> when the games ended — sync to recover the final scores.</>
              }
            </p>
            <button
              onClick={handleSync}
              disabled={isBusy}
              className="shrink-0 flex items-center gap-1.5 px-3 py-1 rounded-lg bg-amber-500 text-white text-xs font-semibold hover:bg-amber-400 disabled:opacity-50 transition-colors"
            >
              <Download size={11} className={syncing ? 'animate-bounce' : ''} />
              {syncing ? 'Syncing…' : 'Sync Now'}
            </button>
          </div>
        )}

        {/* ── Result summary bar ────────────────────────────────────────── */}
        <div className="flex items-center gap-4 px-1 text-xs text-[var(--text)]">
          {stats && !loading && (
            <>
              <span><span className="font-semibold text-[var(--text-h)]">{stats.total}</span> match{stats.total !== 1 ? 'es' : ''}</span>
              {stats.highConf > 0 && (
                <span><span className="font-semibold text-[var(--accent)]">{stats.highConf}</span> high confidence</span>
              )}
              {stats.avgEv !== null && (
                <span>Avg EV: <span className={`font-semibold ${stats.avgEv >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {stats.avgEv >= 0 ? '+' : ''}{stats.avgEv.toFixed(1)}%
                </span></span>
              )}
            </>
          )}
          <button
            onClick={() => setBestPerFixture(v => !v)}
            title={bestPerFixture ? 'Currently showing best signal per game — click to see all signals per game' : 'Currently showing all signals per game — click to show only the best per game'}
            className={`ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[10px] font-semibold transition-colors ${
              bestPerFixture
                ? 'border-[var(--accent)]/50 bg-[var(--accent)]/10 text-[var(--accent)]'
                : 'border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'
            }`}
          >
            <Target size={10} />
            {bestPerFixture ? 'Best per game' : 'All per game'}
          </button>
        </div>

        {loading && signals.length === 0 && (
          <div className="grid gap-4">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="rounded-xl border border-white/8 bg-white/4 h-32 animate-pulse" />
            ))}
          </div>
        )}

        {loading && signals.length > 0 && (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        )}

        {error && signals.length === 0 && (
          <div className="rounded-lg border border-red-500/25 bg-red-500/8 px-6 py-8 text-center">
            <p className="text-sm text-red-400 font-semibold mb-2">Failed to load signals</p>
            <p className="text-xs text-slate-400 mb-4">{typeof error === 'string' ? error : 'Something went wrong. Please try again.'}</p>
            <button onClick={() => window.location.reload()} className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded-lg transition-colors">
              Retry
            </button>
          </div>
        )}

        {error && signals.length > 0 && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
            {error}
          </div>
        )}

        {!loading && !error && displayedSignals.length === 0 && (
          signals.length > 0 ? (
            /* Filters are active but nothing passes — invite the user to loosen them */
            <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-10 flex flex-col items-center gap-2 text-center">
              <span className="text-4xl">🔍</span>
              <p className="text-sm font-semibold text-[var(--text-h)]">No signals match these filters</p>
              <p className="text-xs text-[var(--text)] opacity-75 max-w-xs">
                Try adjusting the <span className="font-semibold text-[var(--accent)]">Min EV%</span> or{' '}
                <span className="font-semibold text-[var(--accent)]">Min Prob%</span> thresholds, changing the confidence filter, or{' '}
                <button onClick={clearAllFilters} className="font-semibold text-[var(--accent)] hover:underline">resetting all filters</button>.
              </p>
            </div>
          ) : (
            /* No signals at all for this date */
            <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-10 flex flex-col items-center gap-3 text-center">
              <span className="text-4xl">📡</span>
              <p className="text-sm font-semibold text-[var(--text-h)]">No signals for {fmtDate(date)}</p>
              <p className="text-xs text-[var(--text)] opacity-75 max-w-sm">
                Odds haven't been pulled for this date yet. Hit{' '}
                <button
                  onClick={handleSync}
                  disabled={isBusy}
                  className="font-semibold text-[var(--accent)] hover:underline disabled:opacity-50"
                >
                  Sync API
                </button>{' '}
                to fetch fixtures and odds, then the engines will score each market for value.
              </p>
              {isToday && (
                <p className="text-[11px] text-[var(--text)] opacity-70 max-w-xs">
                  Syncs run automatically at 06:00, 10:00, 14:00, 18:00 and 23:30 UTC on the production server.
                </p>
              )}
            </div>
          )
        )}

        {!loading && displayedSignals.length > 0 && (
          <div className="space-y-3">
            {/* Visible signals — always shown (all for Pro, first FREE_SIGNAL_LIMIT for free) */}
            {displayedSignals.slice(0, isPro ? undefined : FREE_SIGNAL_LIMIT).map((signal, idx) => (
              <SignalCard
                key={signal.id}
                signal={signal}
                rank={idx + 1}
                isPro={isPro}
                isTracked={trackedKeys.has(`${signal.fixture_id}:${signal.market}`) || autoTrackedKeys.has(`${signal.fixture_id}:${signal.market}`)}
                isAutoTracked={autoTrackedKeys.has(`${signal.fixture_id}:${signal.market}`)}
                onTrackPick={sig => setTrackingSignal({ ...sig, tracking_source_family: trackingSourceFamily() })}
                onDeepDive={onDeepDive}
                oddsAdjPct={settings?.oddsAdjustmentPct ?? 0}
              />
            ))}

            {/* Peek cards — signals 6, 7, 8 shown blurred for free users */}
            {!isPro && displayedSignals.slice(FREE_SIGNAL_LIMIT, FREE_SIGNAL_LIMIT + 3).map((sig, i) => (
              <div key={sig.id || i} className="relative select-none pointer-events-none">
                <div className="opacity-40 blur-sm">
                  <div className="rounded-xl border border-white/8 bg-white/4 h-40 flex flex-col justify-between p-4">
                    <div className="h-3 w-2/3 rounded bg-white/10" />
                    <div className="h-6 w-1/2 rounded bg-white/10" />
                    <div className="h-3 w-1/3 rounded bg-white/10" />
                  </div>
                </div>
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="rounded-lg bg-black/70 px-3 py-1.5 text-xs text-white font-medium backdrop-blur-sm border border-white/10">
                    🔒 Pro only
                  </div>
                </div>
              </div>
            ))}

            {/* Upgrade banner — shown after peek cards when free user has more signals */}
            {!isPro && displayedSignals.length > FREE_SIGNAL_LIMIT && (
              <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/8 px-4 py-3 text-center text-sm">
                <span className="text-slate-300">Viewing <strong className="text-white">5 of {displayedSignals.length}</strong> signals. </span>
                <button onClick={onUpgrade} className="text-indigo-400 hover:text-indigo-300 underline underline-offset-2 font-medium">
                  Upgrade to Pro →
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── VALUE BETS TAB ────────────────────────────────────────────────── */}
      {activeTab === 'valuebets' && (
        <ValueBetsTab
          date={date}
          isPro={isPro}
          onUpgrade={onUpgrade}
          oddsAdjPct={oddsAdjPct}
        />
      )}

      {/* ── AI ADVISORY TAB ───────────────────────────────────────────────── */}
      <div className={activeTab === 'advisor' ? '' : 'hidden'}>
        {isPro ? (
          <AIAdvisorPanel
            date={date}
            tabMode
            onFilterPick={(pick) => {
              if (pick.market) setMarket(pick.market)
              setActiveTab('signals')
            }}
          />
        ) : (
          <UpgradePrompt
            required="pro"
            feature="The AI Advisory Council analyses each day's signals and delivers structured verdicts — Strong, Mixed, or Caution — for each market. Upgrade to Pro to unlock."
            onUpgrade={onUpgrade}
          />
        )}
      </div>

      {/* ── Post-track success toast ──────────────────────────────────────── */}
      {trackedToast && (
        <div className="fixed bottom-20 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-4 py-3 rounded-xl border border-green-500/30 bg-[var(--bg)] shadow-xl text-sm animate-fade-in">
          <span className="text-green-400 font-semibold">✓ Tracked</span>
          <span className="text-[var(--text)] opacity-75 max-w-[220px] truncate">
            {trackedToast.market} · {trackedToast.match}
          </span>
          <button
            onClick={() => { setTrackedToast(null); onNavigateToTracker?.() }}
            className="shrink-0 text-xs font-semibold text-[var(--accent)] hover:underline"
          >
            View in Tracker →
          </button>
          <button onClick={() => setTrackedToast(null)} className="shrink-0 text-[var(--text)] opacity-70 hover:opacity-100">
            <span aria-label="dismiss">✕</span>
          </button>
        </div>
      )}

      {trackingSignal && (
        <TrackModal
          signal={trackingSignal}
          bankroll={settings?.bankroll}
          onClose={() => setTrackingSignal(null)}
          onTracked={() => {
            const sig = trackingSignal
            // Optimistically mark this fixture+market as tracked
            setTrackedKeys(prev => new Set([...prev, `${sig.fixture_id}:${sig.market}`]))
            setTrackingSignal(null)
            setTrackedToast({ market: sig.market, match: `${sig.home_team} vs ${sig.away_team}` })
            setTimeout(() => setTrackedToast(null), 6000)
          }}
        />
      )}

    </div>
  )
}
