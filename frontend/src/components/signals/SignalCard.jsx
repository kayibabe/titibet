import { useState, useEffect } from 'react'
import { ChevronDown, ChevronUp, Target, Clock, TrendingDown, TrendingUp, Lock, CheckCircle2, Lightbulb, X, Heart, Zap, Bot } from 'lucide-react'
import { ConfidenceBadge, AgreementBadge } from './SignalBadge'
import EngineBreakdown from './EngineBreakdown'
import ContradictionAlert from './ContradictionAlert'
import OddsDisplay from '../shared/OddsDisplay'
import { fmtKickoff, marketColor } from '../../utils/format'
import { fetchSignalExplanation } from '../../api/signals'

const FINAL_STATUSES = new Set(['FT', 'AET', 'PEN'])
const LIVE_STATUSES = new Set(['1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE', 'INT'])

function formatFinalScore(homeScore, awayScore) {
  if (homeScore == null || awayScore == null) return null
  return `${homeScore}-${awayScore}`
}

// ── System rank badge ─────────────────────────────────────────────────────────
function RankBadge({ rank }) {
  if (!rank) return null

  if (rank === 1) return (
    <span
      title="System rank #1"
      className="inline-flex items-center justify-center min-w-[1.75rem] h-6 px-2 rounded border border-amber-400/70 bg-amber-400/20 shrink-0 shadow-[0_0_8px_rgba(251,191,36,0.4)]"
    >
      <span className="text-sm font-black tabular-nums tracking-tight text-amber-400">#1</span>
    </span>
  )
  if (rank === 2) return (
    <span
      title="System rank #2"
      className="inline-flex items-center justify-center min-w-[1.75rem] h-6 px-2 rounded border border-slate-400/55 bg-slate-400/15 shrink-0 shadow-[0_0_8px_rgba(148,163,184,0.35)]"
    >
      <span className="text-sm font-black tabular-nums tracking-tight text-slate-300">#2</span>
    </span>
  )
  if (rank === 3) return (
    <span
      title="System rank #3"
      className="inline-flex items-center justify-center min-w-[1.75rem] h-6 px-2 rounded border border-orange-400/55 bg-orange-400/15 shrink-0 shadow-[0_0_8px_rgba(251,146,60,0.35)]"
    >
      <span className="text-sm font-black tabular-nums tracking-tight text-orange-400">#3</span>
    </span>
  )
  return (
    <span
      title={`System rank #${rank}`}
      className="inline-flex items-center justify-center min-w-[1.75rem] h-6 px-2 rounded border border-[var(--border)] shrink-0 opacity-40"
    >
      <span className="text-sm font-bold tabular-nums tracking-tight text-[var(--text)]">#{rank}</span>
    </span>
  )
}

// ── Line movement badge ────────────────────────────────────────────────────────
function DriftBadge({ driftPct }) {
  if (driftPct == null) return null
  if (driftPct < -2) {
    return (
      <span
        title={`Odds shortened ${Math.abs(driftPct).toFixed(1)}% — sharp money confirmed`}
        className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold bg-green-500/15 text-green-400 border border-green-500/30"
      >
        <TrendingDown size={9} />
        Steam
      </span>
    )
  }
  if (driftPct > 4) {
    return (
      <span
        title={`Odds drifted +${driftPct.toFixed(1)}% — market moved against selection`}
        className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold bg-slate-500/10 text-slate-400 border border-slate-500/20"
      >
        <TrendingUp size={9} />
        Drift
      </span>
    )
  }
  return null
}

// ── Cross-module synergy badge — high-conviction combos ──────────────────────
// Fires when two independent models independently confirm the same thesis,
// which is a much stronger signal than either model alone.
// Combo A: BOS SI ≥ 85 + BREA RI₁ < 8%   → structural stability AND low BTTS risk
// Combo B: FHGI FHGMI > 2.5 + BOS passed → strong first-half goal signal with structural support
function SynergyBadge({ adv, market }) {
  if (!adv) return null

  const bosSi   = adv.bos_si
  const bosPassed = adv.bos_passed
  const breaRi1 = adv.brea_ri1
  const fhgmv   = adv.fhgi_fhgmi

  // Combo A: high-conviction defensive / under-goals selection
  if (
    bosSi != null && bosSi >= 85 &&
    breaRi1 != null && breaRi1 < 0.08 &&
    (market === 'BTTS Yes' || market?.startsWith('Under'))
  ) {
    return (
      <span
        title={`High-conviction combo: BOS SI=${bosSi.toFixed(0)} (≥85) + BREA RI₁=${(breaRi1*100).toFixed(1)}% (<8%) — structural stability and low failure probability confirmed by two independent models`}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-amber-500/15 text-amber-300 border-amber-500/40"
      >
        <Zap size={9} />
        High-Conv · Dual confirmed
      </span>
    )
  }

  // Combo B: high-conviction first-half goal selection
  if (
    fhgmv != null && fhgmv > 2.5 &&
    bosPassed &&
    market === 'Over 0.5 1H'
  ) {
    return (
      <span
        title={`High-conviction combo: FHGMI=${fhgmv.toFixed(2)} (>2.50) + BOS structure stable — strong first-half scoring intensity with structural support`}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-violet-500/15 text-violet-300 border-violet-500/40"
      >
        <Zap size={9} />
        High-Conv · FH intensity
      </span>
    )
  }

  return null
}

function MarketIntentBadge({ market }) {
  let label = null
  let style = ''

  if (
    market === 'Over 0.5' ||
    market === 'Over 1.5' ||
    market === 'Over 2.5' ||
    market === 'Over 3.5' ||
    market === 'Home Over 0.5' ||
    market === 'Home Over 1.5' ||
    market === 'Away Over 0.5' ||
    market === 'Away Over 1.5'
  ) {
    label = 'Goals Lean'
    style = 'bg-rose-500/10 text-rose-600 border-rose-500/30'
  } else if (market === 'BTTS Yes' || market === 'BTTS No') {
    label = 'Team Scoring Read'
    style = 'bg-violet-500/10 text-violet-600 border-violet-500/30'
  } else if (
    market === 'Under 1.5' ||
    market === 'Under 2.5' ||
    market === 'Under 3.5'
  ) {
    label = 'Goals Suppression'
    style = 'bg-sky-500/15 text-sky-400 border-sky-500/35'
  } else if (market === '1X (Home or Draw)' || market === 'X2 (Draw or Away)' || market === '12 (Home or Away)') {
    label = 'Safer Market'
    style = 'bg-blue-500/12 text-blue-500 border-blue-500/30'
  } else if (
    market === 'Home Under 0.5' ||
    market === 'Home Under 1.5' ||
    market === 'Away Under 0.5' ||
    market === 'Away Under 1.5'
  ) {
    label = 'Inverse Scoring Angle'
    style = 'bg-sky-500/12 text-sky-400 border-sky-500/30'
  } else if (market === 'Home Win to Nil' || market === 'Away Win to Nil') {
    label = 'Clean Sheet Value'
    style = 'bg-emerald-500/12 text-emerald-600 border-emerald-500/35'
  } else if (market === 'Exactly 1 Goal' || market === 'Exactly 2 Goals' || market === 'Exactly 3 Goals') {
    label = 'High Variance Value'
    style = 'bg-fuchsia-500/10 text-fuchsia-600 border-fuchsia-500/30'
  } else if (market === 'Underdog Over 1.5 Corners') {
    label = 'Corner Pressure'
    style = 'bg-orange-500/10 text-orange-600 border-orange-500/30'
  }

  if (!label) return null
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border tracking-wide ${style}`}>
      {label}
    </span>
  )
}

function getWhyMarketChips(signal) {
  const chips = []
  const market = signal.market
  const prob = signal.bayesian?.prob ?? null
  const drift = signal.odds_drift_pct
  const bookies = signal.bayesian?.bookmaker_count ?? null
  const agreement = signal.dual_agreement
  const tier = signal.league_tier

  if (market === '1X (Home or Draw)' || market === 'X2 (Draw or Away)' || market === '12 (Home or Away)') {
    chips.push('double-chance cover')
  }
  if (
    market === 'Home Under 0.5' ||
    market === 'Home Under 1.5' ||
    market === 'Away Under 0.5' ||
    market === 'Away Under 1.5'
  ) {
    chips.push('inverse scoring angle')
  }
  if (market === 'Home Win to Nil' || market === 'Away Win to Nil') {
    chips.push('clean-sheet setup')
  }
  if (market === 'Exactly 1 Goal' || market === 'Exactly 2 Goals' || market === 'Exactly 3 Goals') {
    chips.push('tight exact-goals cluster')
  }
  if (
    market === 'Over 0.5' || market === 'Over 1.5' || market === 'Over 2.5' || market === 'Over 3.5' ||
    market === 'Home Over 0.5' || market === 'Home Over 1.5' ||
    market === 'Away Over 0.5' || market === 'Away Over 1.5'
  ) {
    chips.push('goals expectation')
  }
  if (market === 'BTTS Yes' || market === 'BTTS No') {
    chips.push('team-scoring read')
  }
  if (agreement === 'Both') {
    chips.push('both engines agree')
  }
  if (prob != null && prob >= 0.7) {
    chips.push('high model probability')
  }
  if (drift != null && drift <= -2) {
    chips.push('sharp drift confirmed')
  }
  if (bookies != null && bookies >= 3) {
    chips.push('broad bookmaker support')
  }
  if (tier === 1) {
    chips.push('tier 1 league')
  }

  // Advanced model enrichment chips
  const adv = signal.advanced
  if (adv?.bos_passed) {
    chips.push('stable match profile')
  }
  if (adv?.brea_ri1 != null && adv.brea_ri1 < 0.07 && market === 'BTTS Yes') {
    chips.push('low BTTS risk')
  }
  if (adv?.fhgi_p_model != null && adv.fhgi_p_model > 0.55 && market === 'Over 0.5 1H') {
    chips.push('FHGI confirmed')
  }
  if (adv?.glicko_r_diff != null && Math.abs(adv.glicko_r_diff) > 150) {
    chips.push('rating gap confirmed')
  }
  if (adv?.zinb_lambda_h != null && adv?.zinb_lambda_a != null) {
    const total = adv.zinb_lambda_h + adv.zinb_lambda_a
    if (total > 2.8 && (market?.startsWith('Over') || market === 'BTTS Yes')) {
      chips.push('high xG match')
    } else if (total < 1.8 && market?.startsWith('Under')) {
      chips.push('low xG match')
    }
  }

  return chips.slice(0, 3)
}

function WhyMarketChips({ signal }) {
  const chips = getWhyMarketChips(signal)
  if (!chips.length) return null
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {chips.map(chip => (
        <span
          key={chip}
          className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-[var(--code-bg)] text-[var(--text)] border border-[var(--border)] opacity-90"
        >
          {chip}
        </span>
      ))}
    </div>
  )
}

// ── Kickoff display — always shows local time; adds live countdown when < 24 h ──
function useKickoff(kickoffAt, status) {
  const localTime = fmtKickoff(kickoffAt)
  const [countdown, setCountdown] = useState('')
  const [urgent, setUrgent] = useState(false)
  const [isLive, setIsLive] = useState(false)
  const [isFinished, setIsFinished] = useState(false)
  const normalizedStatus = (status || '').trim().toUpperCase()

  useEffect(() => {
    if (!kickoffAt && !normalizedStatus) return
    const utcAt = kickoffAt
      ? (kickoffAt.endsWith('Z') || kickoffAt.includes('+') ? kickoffAt : kickoffAt + 'Z')
      : null
    const update = () => {
      if (FINAL_STATUSES.has(normalizedStatus)) {
        setIsFinished(true)
        setIsLive(false)
        setUrgent(false)
        setCountdown('')
        return
      }
      if (LIVE_STATUSES.has(normalizedStatus)) {
        const kickoffMs = utcAt ? new Date(utcAt).getTime() : null
        const staleThreshold = 3 * 60 * 60 * 1000
        if (kickoffMs && Date.now() - kickoffMs > staleThreshold) {
          setIsFinished(true)
          setIsLive(false)
          setUrgent(false)
          setCountdown('')
          return
        }
        setIsFinished(false)
        setIsLive(true)
        setUrgent(true)
        setCountdown('')
        return
      }
      setIsFinished(false)
      const diff = utcAt ? (new Date(utcAt) - Date.now()) : Number.POSITIVE_INFINITY
      if (diff <= 0) {
        setIsLive(false)
        setUrgent(false)
        setCountdown('')
        return
      }
      setIsLive(false)
      const totalMins = Math.floor(diff / 60000)
      setUrgent(totalMins < 180)
      if (totalMins < 24 * 60) {
        const h = Math.floor(totalMins / 60)
        const m = totalMins % 60
        setCountdown(h > 0 ? `${h}h ${m}m` : `${m}m`)
      } else {
        setCountdown('')
      }
    }
    update()
    const id = setInterval(update, 30_000)
    return () => clearInterval(id)
  }, [kickoffAt, normalizedStatus])

  return { localTime, countdown, urgent, isLive, isFinished, statusLabel: normalizedStatus || null }
}

// ── Probability line (primary row) ───────────────────────────────────────────
// Market label + confidence pill | probability bar | % | @odds
// Full bookmaker detail (name, book count, fair-odds compare) stays in Details.
function ProbabilityLine({ market, confidence, prob, odd, bookmaker }) {
  if (prob == null) return null

  const pct = Math.max(0, Math.min(100, prob * 100))
  const pctLabel = Math.round(pct)

  const confColor = {
    High:   'bg-green-500',
    Medium: 'bg-yellow-500',
    Low:    'bg-slate-500',
  }[confidence] || 'bg-[var(--code-bg)]'

  const barColor =
    pct >= 70 ? 'bg-green-500'  :
    pct >= 50 ? 'bg-yellow-500' :
    pct >= 35 ? 'bg-orange-500' :
    'bg-red-500'

  const showConfPill = confidence && confidence !== 'None'

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 min-w-0 shrink-0">
        <span className={`text-sm font-medium truncate ${marketColor(market)}`}>{market}</span>
        {showConfPill && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold text-white ${confColor}`}>
            {confidence}
          </span>
        )}
      </div>

      <div
        className="w-20 shrink-0 h-2 bg-[var(--code-bg)] rounded-full overflow-hidden"
        role="progressbar"
        aria-valuenow={pctLabel}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${market} probability ${pctLabel}%`}
      >
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <span className="text-sm font-bold font-mono text-[var(--text-h)] w-12 text-right tabular-nums shrink-0">
        {pctLabel}%
      </span>

      {odd != null && odd > 1 && (
        <span
          title={bookmaker ? `Best market price · ${bookmaker}` : 'Best market price'}
          className="text-sm font-bold font-mono text-[var(--accent)] tabular-nums shrink-0"
        >
          @{Number(odd).toFixed(2)}
        </span>
      )}
    </div>
  )
}

// ── Fair odds display ─────────────────────────────────────────────────────────
function FairOddsRow({ bayesian, bestOdd, bookmaker }) {
  const fairOdds = bayesian?.prob ? (1 / bayesian.prob).toFixed(2) : null
  const overroundPct = bayesian?.overround
    ? ((bayesian.overround - 1) * 100).toFixed(1)
    : null

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {fairOdds && (
        <span className="text-xs text-[var(--text)]">
          Fair:{' '}
          <span className="font-mono font-semibold text-[var(--text-h)]">{fairOdds}</span>
        </span>
      )}
      {fairOdds && bestOdd && (
        <span className="text-xs text-[var(--text)] opacity-65">→</span>
      )}
      <OddsDisplay odds={bestOdd} bookmaker={bookmaker} />
      {overroundPct && (
        <span className={`text-xs font-medium ${
          parseFloat(overroundPct) < 5
            ? 'text-green-400'
            : parseFloat(overroundPct) < 10
              ? 'text-[var(--text-h)]'
              : 'text-amber-500'
        }`}>
          {overroundPct}% margin
        </span>
      )}
    </div>
  )
}

// ── Locked metric pill — shown to free users in place of EV/Quality/Stake ─────
function LockedPill({ label }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold border border-blue-500/25 bg-blue-500/8 text-blue-400 select-none">
      <Lock size={8} />
      {label}
    </span>
  )
}

// ── Inline explanation panel ──────────────────────────────────────────────────
function ExplainPanel({ fixtureId, market, onClose }) {
  const [data, setData]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchSignalExplanation(fixtureId, market)
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [fixtureId, market])

  return (
    <div className="mx-0 mt-0 px-4 pb-4 pt-3 border-t border-[var(--border)] bg-[var(--code-bg)] rounded-b-xl space-y-2">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-[var(--accent)]">
          <Lightbulb size={12} />
          Why this signal?
        </span>
        <button onClick={onClose} className="text-[var(--text)] opacity-50 hover:opacity-100">
          <X size={13} />
        </button>
      </div>
      {loading && <p className="text-xs text-[var(--text)] opacity-60 animate-pulse">Generating explanation…</p>}
      {error   && <p className="text-xs text-red-400">{error}</p>}
      {data?.paragraphs?.map((p, i) => (
        <p key={i} className="text-xs text-[var(--text)] leading-relaxed opacity-85"
           dangerouslySetInnerHTML={{ __html: p.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>') }}
        />
      ))}
    </div>
  )
}


const SAVED_KEY = 'titibet_saved_signals_v1'
const getSaved = () => { try { return JSON.parse(localStorage.getItem(SAVED_KEY) || '[]') } catch { return [] } }

// ── Main card ─────────────────────────────────────────────────────────────────
// Persist engine-breakdown open/closed across cards for the session, so power
// users who want the math don't have to re-open it on every card.
const ENGINE_OPEN_KEY = 'titibet_engine_breakdown_open_v1'
const getEngineOpen = () => { try { return sessionStorage.getItem(ENGINE_OPEN_KEY) === '1' } catch { return false } }

export default function SignalCard({ signal, rank, isPro = true, isTracked = false, isAutoTracked = false, onTrackPick, onDeepDive }) {
  const [expanded, setExpanded]       = useState(getEngineOpen)
  const [showDetails, setShowDetails] = useState(false)
  const [showExplain, setShowExplain] = useState(false)
  const [isSaved, setIsSaved] = useState(() => getSaved().includes(signal.id))

  const toggleSave = (e) => {
    e.stopPropagation()
    const saved = getSaved()
    const next = isSaved
      ? saved.filter(id => id !== signal.id)
      : [...saved, signal.id]
    localStorage.setItem(SAVED_KEY, JSON.stringify(next))
    setIsSaved(!isSaved)
  }

  const { localTime, countdown, urgent, isLive, isFinished, statusLabel } = useKickoff(signal.kickoff_at, signal.status)
  const finalScore = formatFinalScore(signal.home_score, signal.away_score)

  const stakePct = signal.dual_recommended_stake_pct != null
    ? `${(signal.dual_recommended_stake_pct * 100).toFixed(1)}%`
    : null

  // Convert raw quality score to an A/B/C/D letter grade.
  // Thresholds match the probability-based quality scale introduced 2026-07-02
  // (quality ≈ prob × tier/bookmaker/confidence factors, typically 0.2–0.8) —
  // same cutoffs as the backend auto-tracker grade.
  const qualityGrade = (() => {
    const q = signal.dual_quality_score
    if (q == null) return null
    const rawScore = q.toFixed(4)
    if (q >= 0.60) return { label: 'A', color: 'text-green-400',  bg: 'bg-green-500/15  border-green-500/30',  rawScore }
    if (q >= 0.45) return { label: 'B', color: 'text-blue-400',   bg: 'bg-blue-500/15   border-blue-500/30',   rawScore }
    if (q >= 0.30) return { label: 'C', color: 'text-amber-400',  bg: 'bg-amber-500/15  border-amber-500/30',  rawScore }
    return              { label: 'D', color: 'text-slate-400',  bg: 'bg-slate-500/10  border-slate-500/20',  rawScore }
  })()

  const isContradiction = signal.dual_agreement === 'Contradiction'
  const isBayesianOnly = signal.dual_agreement === 'Bayesian Only' || (signal.dual_agreement === 'Both' && signal.dual_confidence !== 'High')
  const displayBestOdd = signal.best_odd ?? signal.bayesian?.best_odd ?? null
  const displayBookmaker = signal.best_bookmaker ?? signal.bayesian?.bookmaker ?? null
  const primaryProb = Math.max(signal.bayesian?.prob ?? 0, signal.poisson?.prob ?? 0)
  const isMediumConfidence = signal.dual_confidence === 'Medium'
  const isHighProbabilityOutcome = primaryProb >= 0.7 && !isMediumConfidence
  const isUnderMarket = ['Under 1.5', 'Under 2.5', 'Under 3.5', 'Away Under 1.5', 'Home Under 1.5'].includes(signal.market)

  return (
    <div className={`rounded-xl border shadow-sm overflow-hidden transition-colors ${
      isContradiction
        ? 'border-red-400/40 border-l-4 border-l-red-400'
        : isBayesianOnly
          ? 'border-violet-400/35 border-l-4 border-l-violet-400'
          : isUnderMarket
            ? 'border-sky-400/40 border-l-4 border-l-sky-400'
            : isHighProbabilityOutcome
              ? 'border-emerald-500/40 border-l-4 border-l-emerald-500'
              : isMediumConfidence
                ? 'border-amber-400/30 hover:border-amber-400/50'
                : 'border-[var(--border)] hover:border-[var(--accent)]/40'
    }`}>

      {/* ── Header ── */}
      <div className="px-4 py-3 space-y-2.5">

        {/* ── PRIMARY: Match name — the first thing the eye lands on ── */}
        <div className="flex items-start justify-between gap-2">
          <h3
            className="text-base font-semibold text-[var(--text-h)] leading-tight cursor-pointer hover:text-[var(--accent)] transition-colors"
            onClick={() => onDeepDive?.(signal.fixture_id)}
          >
            {signal.home_team} vs {signal.away_team}
          </h3>

          {/* Status + rank float right */}
          <div className="flex items-center gap-1.5 shrink-0 pt-0.5">
            {isLive && (
              <span className="inline-flex items-center gap-1 text-xs font-bold text-red-400 animate-pulse">
                <Clock size={10} />
                LIVE
              </span>
            )}
            {isFinished && (
              <span className="inline-flex items-center gap-1 text-xs font-bold text-emerald-500">
                <Clock size={10} />
                {statusLabel || 'FT'}
              </span>
            )}
            {isFinished && finalScore && (
              <span
                title="Final score"
                className="inline-flex items-center rounded-md border border-emerald-500/25 bg-emerald-500/8 px-2 py-0.5 text-xs font-semibold text-emerald-700"
              >
                {finalScore}
              </span>
            )}
            {!isLive && !isFinished && localTime && (
              <span className={`inline-flex items-center gap-1 text-xs font-medium ${
                urgent ? 'text-amber-400' : 'text-[var(--text)] opacity-75'
              }`}>
                <Clock size={10} className={urgent ? 'animate-pulse' : ''} />
                {localTime}
                {countdown && (
                  <span className={`ml-0.5 ${urgent ? 'text-amber-400' : 'opacity-60'}`}>
                    ({countdown})
                  </span>
                )}
              </span>
            )}
            <RankBadge rank={rank} />
          </div>
        </div>

        {/* ── PRIMARY: Market label · Confidence pill · Probability bar · % ── */}
        <ProbabilityLine
          market={signal.market}
          confidence={signal.dual_confidence}
          prob={primaryProb > 0 ? primaryProb : null}
          odd={displayBestOdd}
          bookmaker={displayBookmaker}
        />

        {/* One synthesized conviction chip on the face — fires only when two
            independent models confirm the same thesis. A real decision input. */}
        {isPro && <SynergyBadge adv={signal.advanced} market={signal.market} />}

        {isContradiction && (
          <ContradictionAlert mixedSignals={signal.poisson?.mixed_signals} />
        )}

        {/* ── SECONDARY: collapsed by default — expand with "Details" ── */}
        <button
          onClick={() => setShowDetails(v => !v)}
          aria-label={showDetails ? 'Hide details' : 'Show details'}
          aria-expanded={showDetails}
          className="flex items-center gap-1 text-[11px] text-[var(--text)] opacity-50 hover:opacity-90 transition-opacity"
        >
          {showDetails ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          {showDetails ? 'Hide details' : 'Details'}
        </button>

        {showDetails && (
          <div className="space-y-2 border-t border-[var(--border)] pt-2.5">

            {/* Bookmaker · offered odds · bookmaker count */}
            {displayBestOdd != null && (
              <div className="flex items-center gap-2 flex-wrap text-xs text-[var(--text)]">
                <span className="opacity-75">{displayBookmaker}</span>
                <span className="font-mono text-[var(--accent)] font-semibold">
                  {Number(displayBestOdd).toFixed(2)}
                </span>
                {signal.bayesian?.bookmaker_count != null && (
                  <span className="opacity-50">
                    · {signal.bayesian.bookmaker_count} {signal.bayesian.bookmaker_count === 1 ? 'book' : 'books'}
                  </span>
                )}
              </div>
            )}

            {/* Agreement · Drift · MarketIntent */}
            <div className="flex items-center gap-2 flex-wrap">
              <AgreementBadge agreement={signal.dual_agreement} />
              {isPro && <DriftBadge driftPct={signal.odds_drift_pct} />}
              <MarketIntentBadge market={signal.market} />
            </div>

            {/* Advanced-model diagnostics now live one layer deeper, in the
                Engine breakdown panel — keeping the Details layer decision-focused. */}

            {/* Fair odds → offered · book margin */}
            <FairOddsRow
              bayesian={signal.bayesian}
              bestOdd={displayBestOdd}
              bookmaker={displayBookmaker}
            />

            {/* Why-market context chips */}
            <WhyMarketChips signal={signal} />

            {/* League · tier dot · country */}
            <div className="flex items-center gap-2 flex-wrap text-xs text-[var(--text)] opacity-75">
              {signal.league_tier != null && (
                <span
                  title={`Tier ${signal.league_tier} league`}
                  className={`inline-block w-2 h-2 rounded-full shrink-0 ${
                    signal.league_tier === 1 ? 'bg-amber-400' :
                    signal.league_tier === 2 ? 'bg-slate-400' :
                    'bg-slate-600'
                  }`}
                />
              )}
              {signal.league && (
                <span>
                  {signal.country && <span className="opacity-60">{signal.country} · </span>}
                  {signal.league}
                </span>
              )}
            </div>

            {/* Stake recommendation (pro) or locked pill (free) */}
            {isPro ? (
              stakePct && !isContradiction ? (
                <div className="text-xs text-[var(--text)]">
                  Stake: <span className="font-semibold text-[var(--accent)]">{stakePct}</span>
                </div>
              ) : null
            ) : (
              <div className="flex items-center gap-2">
                <LockedPill label="Stake %" />
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Footer ── */}
      <div className="px-5 py-2.5 border-t border-[var(--border)] flex items-center gap-2 flex-wrap">
        {isPro ? (
          <button
            onClick={() => setExpanded(v => { const next = !v; try { sessionStorage.setItem(ENGINE_OPEN_KEY, next ? '1' : '0') } catch { /* ignore */ } return next })}
            aria-label="Toggle engine breakdown"
            aria-expanded={expanded}
            className="flex items-center gap-1 text-xs text-[var(--text)] hover:text-[var(--accent)] transition-colors"
          >
            {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            Engine breakdown
          </button>
        ) : (
          <span className="flex items-center gap-1 text-xs text-blue-400 opacity-75 select-none">
            <Lock size={11} />
            Engine breakdown · Pro
          </span>
        )}

        {/* Explain This Signal — available to all users */}
        <button
          onClick={() => setShowExplain(v => !v)}
          aria-label="Why this signal?"
          className={`flex items-center gap-1 text-xs transition-colors ${
            showExplain
              ? 'text-[var(--accent)]'
              : 'text-[var(--text)] opacity-60 hover:opacity-100'
          }`}
          title="Plain-English explanation of why this signal was generated"
        >
          <Lightbulb size={12} />
          Why?
        </button>

        <div className="flex-1" />

        {/* Save to watchlist */}
        <button
          onClick={toggleSave}
          aria-label={isSaved ? 'Remove from saved' : 'Save signal'}
          className={`p-1.5 rounded-lg transition-colors ${isSaved ? 'text-red-400 hover:text-red-300' : 'text-slate-500 hover:text-slate-300'}`}
        >
          <Heart size={15} fill={isSaved ? 'currentColor' : 'none'} />
        </button>

        {isAutoTracked && (
          <span
            title="This signal was automatically recorded as a system pick. View in Tracker to see results."
            className="flex items-center gap-1 text-xs font-semibold text-violet-400 mr-1"
          >
            <Bot size={13} />
            System Pick
          </span>
        )}
        {!isAutoTracked && isTracked && (
          <span className="flex items-center gap-1 text-xs font-semibold text-emerald-400 mr-1">
            <CheckCircle2 size={13} />
            Tracked
          </span>
        )}
      </div>

      {/* ── Explanation panel (inline, deterministic) ── */}
      {showExplain && (
        <ExplainPanel
          fixtureId={signal.fixture_id}
          market={signal.market}
          onClose={() => setShowExplain(false)}
        />
      )}

      {/* ── Expanded engine breakdown (Pro only, tertiary layer) ── */}
      {expanded && isPro && (
        <div className="px-5 pb-5 pt-3 border-t border-[var(--border)] bg-[var(--code-bg)] space-y-3">
          {/* Quality grade lives here in the tertiary layer */}
          {qualityGrade && (
            <div className="flex items-center gap-2">
              <span
                title={`Signal quality grade (A = strongest, D = weakest). Raw score: ${qualityGrade.rawScore}`}
                className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-bold ${qualityGrade.bg} ${qualityGrade.color}`}
              >
                {qualityGrade.label}
              </span>
              <span className="text-[10px] text-[var(--text)] opacity-50">Quality grade</span>
            </div>
          )}
          <EngineBreakdown signal={signal} />
        </div>
      )}
    </div>
  )
}
