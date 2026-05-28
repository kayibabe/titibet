import { useState, useEffect, useMemo } from 'react'
import { TrendingUp, Calendar, RefreshCw, Zap } from 'lucide-react'
import { fetchSignals } from '../api/signals'
import useTier from '../hooks/useTier'
import { useSettings } from '../store/useSettings'

function adjustOdd(raw, pct) {
  if (raw == null || !pct) return raw
  return Math.max(1.01, raw * (1 - Math.abs(pct) / 100))
}

const FREE_LIMIT = 5

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function fmtDate(iso) {
  if (!iso) return ''
  const d = new Date(iso + 'T00:00:00')
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
}

function fmtKickoff(iso) {
  if (!iso) return null
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

// ── Odds tier config ───────────────────────────────────────────────────────────
const ODDS_TIERS = [
  { min: 1.5,  label: '1.5+', desc: 'Conservative' },
  { min: 2.0,  label: '2.0+', desc: 'Moderate'     },
  { min: 2.5,  label: '2.5+', desc: 'Standard'     },
  { min: 3.0,  label: '3.0+', desc: 'Value'        },
  { min: 3.5,  label: '3.5+', desc: 'Aggressive'   },
  { min: 4.0,  label: '4.0+', desc: 'Long shot'    },
]

// ── Value Bet Card ─────────────────────────────────────────────────────────────
function ValueBetCard({ signal, rank, oddsAdjPct = 0 }) {
  const b = signal.bayesian || {}
  const adjOdd      = adjustOdd(b.best_odd, oddsAdjPct)
  const impliedProb = adjOdd ? Math.round((1 / adjOdd) * 100) : null
  const modelProb   = b.prob ? Math.round(b.prob * 100) : null
  const edge        = modelProb && impliedProb ? modelProb - impliedProb : null
  const evPct = oddsAdjPct && b.prob != null && adjOdd != null
    ? (b.prob * adjOdd - 1) * 100
    : b.ev_pct ?? null

  const FINAL = new Set(['FT', 'AET', 'PEN'])
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
                {adjOdd?.toFixed(2) ?? '—'}
              </p>
              <p className="text-[9px] text-[var(--text)] opacity-70 mt-0.5">
                {oddsAdjPct ? `adj. −${oddsAdjPct}%` : (b.bookmaker || 'Pinnacle')}
              </p>
            </div>
          </div>
        </div>

        {/* Metrics row */}
        <div className="grid grid-cols-3 gap-2">
          {/* EV% */}
          <div className="rounded-lg bg-[var(--code-bg)] p-2 text-center">
            <p className={`text-sm font-bold tabular-nums ${evPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {evPct != null ? `+${evPct.toFixed(1)}%` : '—'}
            </p>
            <p className="text-[9px] text-[var(--text)] opacity-70 mt-0.5">EV</p>
          </div>

          {/* Model prob */}
          <div className="rounded-lg bg-[var(--code-bg)] p-2 text-center">
            <p className="text-sm font-bold tabular-nums text-[var(--text-h)]">
              {modelProb != null ? `${modelProb}%` : '—'}
            </p>
            <p className="text-[9px] text-[var(--text)] opacity-70 mt-0.5">Model Prob</p>
          </div>

          {/* Edge */}
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

// ── Main page ──────────────────────────────────────────────────────────────────
export default function ValueBetsPage({ onUpgrade }) {
  const { isPro } = useTier()
  const { settings } = useSettings()
  const oddsAdjPct = settings?.oddsAdjustmentPct ?? 0
  const today = todayStr()

  const [date,        setDate]        = useState(today)
  const [minOdds,     setMinOdds]     = useState(1.5)
  const [allSignals,  setAllSignals]  = useState([])
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState(null)
  const [showPicker,  setShowPicker]  = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchSignals({ date, sort_by: 'system' })
      .then(data => setAllSignals(Array.isArray(data) ? data : []))
      .catch(e  => setError(e.message))
      .finally(() => setLoading(false))
  }, [date])

  // Value bets = is_value true AND adjusted best_odd >= minOdds, sorted by EV desc
  const valueBets = useMemo(() => {
    return allSignals
      .filter(s => {
        const adj = adjustOdd(s.bayesian?.best_odd, oddsAdjPct)
        return s.bayesian?.is_value && (adj ?? 0) >= minOdds
      })
      .sort((a, b) => {
        const evA = oddsAdjPct && a.bayesian?.prob != null
          ? (a.bayesian.prob * (adjustOdd(a.bayesian.best_odd, oddsAdjPct) ?? 0) - 1) * 100
          : (a.bayesian?.ev_pct ?? 0)
        const evB = oddsAdjPct && b.bayesian?.prob != null
          ? (b.bayesian.prob * (adjustOdd(b.bayesian.best_odd, oddsAdjPct) ?? 0) - 1) * 100
          : (b.bayesian?.ev_pct ?? 0)
        return evB - evA
      })
  }, [allSignals, minOdds, oddsAdjPct])

  const displayed  = isPro ? valueBets : valueBets.slice(0, FREE_LIMIT)
  const lockedCount = valueBets.length - displayed.length
  const avgEv = valueBets.length
    ? (valueBets.reduce((s, x) => {
        const adj = adjustOdd(x.bayesian?.best_odd, oddsAdjPct)
        const ev = oddsAdjPct && x.bayesian?.prob != null && adj != null
          ? (x.bayesian.prob * adj - 1) * 100
          : (x.bayesian?.ev_pct ?? 0)
        return s + ev
      }, 0) / valueBets.length).toFixed(1)
    : null

  return (
    <div className="space-y-6 pb-24 lg:pb-8">

      {/* Page header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Zap size={18} className="text-[var(--accent)]" />
          <h1 className="text-xl font-bold text-[var(--text-h)]">Value Bets</h1>
        </div>
        <p className="text-sm text-[var(--text)] opacity-70 max-w-xl">
          Bets where our dual-engine model probability beats the bookmaker's implied probability —
          positive expected value identified by both Bayesian and Poisson engines.
        </p>
      </div>

      {/* Date picker */}
      <div className="flex items-center gap-2">
        <div className="relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] cursor-pointer hover:bg-[var(--code-bg)] transition-colors"
          onClick={() => document.getElementById('vb-date-input')?.showPicker()}>
          <Calendar size={13} className="text-[var(--accent)] shrink-0" />
          <span className="text-sm font-medium text-[var(--text-h)]">
            {fmtDate(date)}{date === today ? ' · Today' : ''}
          </span>
          <input
            id="vb-date-input"
            type="date"
            value={date}
            max={today}
            onChange={e => e.target.value && setDate(e.target.value)}
            style={{ position: 'absolute', opacity: 0, pointerEvents: 'none', width: 0, height: 0 }}
          />
        </div>
        {loading && <RefreshCw size={13} className="animate-spin text-[var(--text)] opacity-65" />}
      </div>

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
            <button
              key={tier.min}
              onClick={() => setMinOdds(tier.min)}
              title={tier.desc}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-semibold transition-colors ${
                selected
                  ? 'border-[var(--accent)] bg-[var(--accent-bg)] text-[var(--accent)]'
                  : 'border-[var(--border)] text-[var(--text)] hover:border-[var(--accent)]/50 hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'
              }`}
            >
              {tier.label}
              {!loading && count > 0 && (
                <span className={`text-[10px] font-bold tabular-nums ${selected ? 'opacity-80' : 'text-emerald-400'}`}>
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Results summary bar */}
      {!loading && !error && valueBets.length > 0 && (
        <div className="flex items-center gap-4 text-xs text-[var(--text)] opacity-70">
          <span><span className="font-semibold text-[var(--text-h)]">{valueBets.length}</span> value bet{valueBets.length !== 1 ? 's' : ''}</span>
          {avgEv && <span>Avg EV: <span className="font-semibold text-emerald-400">+{avgEv}%</span></span>}
          <span className="ml-auto">Sorted by highest EV</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">{error}</div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 animate-pulse">
          {[1,2,3,4,5,6].map(i => (
            <div key={i} className="h-40 rounded-xl bg-[var(--border)]" />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && valueBets.length === 0 && (
        <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-12 flex flex-col items-center gap-3 text-center">
          <span className="text-4xl">🔍</span>
          <p className="text-sm font-semibold text-[var(--text-h)]">No value bets at {minOdds}+ odds</p>
          <p className="text-xs text-[var(--text)] opacity-80 max-w-xs">
            Try lowering the minimum odds, or check a different date.
          </p>
        </div>
      )}

      {/* Bet cards grid */}
      {!loading && displayed.length > 0 && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
            {displayed.map((signal, i) => (
              <ValueBetCard key={signal.id} signal={signal} rank={i + 1} oddsAdjPct={oddsAdjPct} />
            ))}
          </div>

          {/* Locked preview for free users — gradient fade with upgrade callout */}
          {lockedCount > 0 && (
            <div className="relative" style={{ minHeight: '180px' }}>
              {/* Ghost cards — visible but fading out via gradient mask */}
              <div
                className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 pointer-events-none select-none"
                style={{
                  maskImage: 'linear-gradient(to bottom, rgba(0,0,0,0.6) 0%, rgba(0,0,0,0) 100%)',
                  WebkitMaskImage: 'linear-gradient(to bottom, rgba(0,0,0,0.6) 0%, rgba(0,0,0,0) 100%)',
                }}
              >
                {valueBets.slice(FREE_LIMIT, FREE_LIMIT + 3).map((_, i) => (
                  <div key={i} className="rounded-xl border border-white/8 bg-white/4 h-20 opacity-50" />
                ))}
              </div>
              {/* Upgrade callout overlaid at the bottom */}
              <div className="absolute bottom-0 left-0 right-0 text-center pb-4">
                <p className="text-sm font-semibold text-white mb-1">
                  Unlock {lockedCount} more value bet{lockedCount !== 1 ? 's' : ''} with Pro
                </p>
                <p className="text-xs text-slate-400 mb-3">
                  Pro users see all value bets across 50+ markets daily
                </p>
                <button
                  onClick={onUpgrade}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded-lg transition-colors font-medium"
                >
                  Upgrade to Pro
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
