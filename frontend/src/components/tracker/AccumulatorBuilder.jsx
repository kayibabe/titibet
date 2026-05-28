import { useState, useEffect, useMemo } from 'react'
import { Plus, Trash2, Sparkles, Layers, X } from 'lucide-react'
import { createAccumulator, confirmRecommendedTicket } from '../../api/tracker'
import { fetchSignals, fetchRecommendedTickets } from '../../api/signals'
import { fetchAdvisorInsights } from '../../api/advisor'
import { useAccaDraft, clearAccaDraft } from '../../store/useAccaDraft'

const FINAL_STATUSES = new Set(['FT', 'AET', 'PEN'])

// ── Date helper ───────────────────────────────────────────────────────────────
function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// ── Signal → leg mapper (shared by FromSignals and FromAI builders) ───────────
function signalToLeg(signal, odds) {
  const today = todayStr()
  return {
    signal_id:             signal.id,
    fixture_id:            signal.fixture_id,
    match_name:            `${signal.home_team} vs ${signal.away_team}`,
    home_team:             signal.home_team,
    away_team:             signal.away_team,
    league:                signal.league,
    league_tier:           signal.league_tier,
    kickoff_at:            signal.kickoff_at,
    event_date:            signal.kickoff_at ? signal.kickoff_at.slice(0, 10) : today,
    market:                signal.market,
    selection_name:        signal.selection_name || signal.market,
    bookmaker:             signal.bayesian?.bookmaker ?? null,
    odds,
    probability:           signal.bayesian?.prob ?? null,
    ev_pct:                signal.bayesian?.ev_pct ?? null,
    confidence:            signal.dual_confidence,
    agreement:             signal.dual_agreement,
    recommended_stake_pct: signal.dual_recommended_stake_pct ?? null,
    source_rule_key:       signal.poisson?.rule_key ?? null,
    signal_grade:          signal.poisson?.grade ?? null,
    why_tags:              [],
  }
}

// ── Shared helpers ────────────────────────────────────────────────────────────
function OddsPill({ odds }) {
  return (
    <span className="rounded bg-[var(--code-bg)] px-1.5 py-0.5 font-mono text-xs text-[var(--accent)]">
      {odds?.toFixed(2)}
    </span>
  )
}

function CombinedOddsBar({ odds }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-[var(--text)] opacity-85">Combined odds:</span>
      <span className="text-lg font-semibold text-[var(--accent)]">{odds.toFixed(2)}</span>
    </div>
  )
}

// ── Source toggle ─────────────────────────────────────────────────────────────
function SourceToggle({ source, onChange }) {
  return (
    <div className="flex rounded-lg border border-[var(--border)] overflow-hidden text-xs font-semibold">
      {[
        { id: 'ai', label: 'From AI', icon: Sparkles },
      ].map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          onClick={() => onChange(id)}
          className={`flex items-center gap-1.5 px-3 py-1.5 transition-colors ${
            source === id
              ? 'bg-[var(--accent)] text-white'
              : 'text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'
          }`}
        >
          <Icon size={11} />
          {label}
        </button>
      ))}
    </div>
  )
}

// ── From Bets builder (original logic) ───────────────────────────────────────
function FromBetsBuilder({ pendingBets = [], onCreated }) {
  const [legs, setLegs] = useState([])
  const [stake, setStake] = useState(10)
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const combinedOdds = legs.reduce((acc, leg) => acc * (leg.odds || 1), 1)

  function addLeg(bet) {
    if (legs.find(l => l.bet_id === bet.id)) return
    const label = bet.home_team && bet.away_team
      ? `${bet.home_team} vs ${bet.away_team}`
      : bet.match_name
    setLegs(prev => [...prev, { bet_id: bet.id, odds: bet.odds, label: `${label} - ${bet.market_type}` }])
  }

  function removeLeg(bet_id) {
    setLegs(prev => prev.filter(l => l.bet_id !== bet_id))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (legs.length < 2) { setError('Select at least 2 legs'); return }
    setSaving(true)
    setError(null)
    try {
      await createAccumulator({
        name: name || undefined,
        stake: parseFloat(stake),
        legs: legs.map((l, i) => ({ tracked_bet_id: l.bet_id, leg_order: i })),
      })
      setLegs([])
      setName('')
      onCreated?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const availableBets = pendingBets.filter(b => !legs.find(l => l.bet_id === b.id))

  return (
    <div className="space-y-4">
      <div>
        <p className="mb-2 text-xs font-medium text-[var(--text)] opacity-85">
          {availableBets.length > 0 ? 'Click a pending bet to add it:' : 'No pending bets — track some picks first'}
        </p>
        <div className="max-h-48 space-y-1 overflow-y-auto pr-1">
          {availableBets.map(bet => {
            const label = bet.home_team && bet.away_team
              ? `${bet.home_team} vs ${bet.away_team}`
              : bet.match_name
            return (
              <button
                key={bet.id}
                type="button"
                onClick={() => addLeg(bet)}
                className="flex w-full items-center justify-between gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-left text-xs transition-colors hover:border-[var(--accent)] hover:bg-[var(--accent-bg)]"
              >
                <span className="truncate">
                  {label} — <span className="text-[var(--text)] opacity-85">{bet.market_type}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <OddsPill odds={bet.odds} />
                  <Plus size={11} className="text-[var(--accent)]" />
                </span>
              </button>
            )
          })}
        </div>
      </div>

      {legs.length > 0 && (
        <div className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--code-bg)] p-3">
          <p className="text-xs font-medium text-[var(--text-h)]">Selected ({legs.length})</p>
          {legs.map(leg => (
            <div key={leg.bet_id} className="flex items-center gap-2 text-xs">
              <span className="flex-1 truncate text-[var(--text)]">{leg.label}</span>
              <OddsPill odds={leg.odds} />
              <button type="button" onClick={() => removeLeg(leg.bet_id)} className="ml-1 text-red-400 hover:text-red-300">
                <Trash2 size={11} />
              </button>
            </div>
          ))}
          <div className="border-t border-[var(--border)] pt-1">
            <CombinedOddsBar odds={combinedOdds} />
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-[var(--text)]">
          <span className="font-medium">Name (optional)</span>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="My Acca"
            className="rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-sm text-[var(--text-h)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-[var(--text)]">
          <span className="font-medium">Stake (K)</span>
          <input
            type="number" min="0.01" step="0.01"
            value={stake}
            onChange={e => setStake(e.target.value)}
            className="w-24 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-sm text-[var(--text-h)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
        <button
          type="submit"
          disabled={saving || legs.length < 2}
          className="self-end rounded-lg bg-[var(--accent)] px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save Acca'}
        </button>
      </form>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  )
}

// ── From Signals builder ──────────────────────────────────────────────────────
function FromSignalsBuilder({ onCreated, defaultStake = 10 }) {
  const [signals,     setSignals]     = useState([])
  const [loadingS,    setLoadingS]    = useState(false)
  const [fetchError,  setFetchError]  = useState(null)
  const [legs,        setLegs]        = useState([])   // [{ signal, odds }]
  const [stake,       setStake]       = useState(defaultStake)
  const [name,        setName]        = useState('')
  const [saving,      setSaving]      = useState(false)
  const [saveError,   setSaveError]   = useState(null)
  const [confFilter,  setConfFilter]  = useState('')   // '' | 'High' | 'Medium'

  // Load today's signals once
  useEffect(() => {
    setLoadingS(true)
    setFetchError(null)
    fetchSignals({ date: todayStr(), sort_by: 'system' })
      .then(data => setSignals(Array.isArray(data) ? data : []))
      .catch(e => setFetchError(e.message))
      .finally(() => setLoadingS(false))
  }, [])

  const legIds = new Set(legs.map(l => l.signal.id))

  const available = signals.filter(s => {
    if (legIds.has(s.id)) return false
    if (confFilter && s.dual_confidence !== confFilter) return false
    return true
  })

  const combinedOdds = legs.reduce((acc, l) => acc * (l.odds || 1), 1)

  function addLeg(signal) {
    if (legIds.has(signal.id)) return
    const odds = signal.bayesian?.best_odd ?? null
    if (!odds) return                           // no odds — skip
    setLegs(prev => [...prev, { signal, odds }])
  }

  function removeLeg(signalId) {
    setLegs(prev => prev.filter(l => l.signal.id !== signalId))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (legs.length < 2) { setSaveError('Select at least 2 legs'); return }
    setSaving(true)
    setSaveError(null)
    try {
      await confirmRecommendedTicket({
        card_key:    'custom',
        stake:       parseFloat(stake),
        ticket_date: todayStr(),
        name:        name || undefined,
        legs:        legs.map(l => signalToLeg(l.signal, l.odds)),
      })
      setLegs([])
      setName('')
      onCreated?.()
    } catch (e) {
      setSaveError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loadingS) {
    return (
      <div className="space-y-2 animate-pulse">
        {[1, 2, 3].map(i => (
          <div key={i} className="h-9 rounded-lg bg-[var(--border)]" />
        ))}
      </div>
    )
  }

  if (fetchError) {
    return <p className="text-xs text-red-400">{fetchError}</p>
  }

  return (
    <div className="space-y-4">
      {/* Confidence quick-filter */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-[var(--text)] opacity-75 shrink-0">Filter:</span>
        {['', 'High', 'Medium', 'Low'].map(c => (
          <button
            key={c}
            type="button"
            onClick={() => setConfFilter(c)}
            className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
              confFilter === c
                ? 'bg-[var(--accent)] text-white border-[var(--accent)]'
                : 'border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'
            }`}
          >
            {c || 'All'}
          </button>
        ))}
        <span className="ml-auto text-xs text-[var(--text)] opacity-70">{available.length} available</span>
      </div>

      {/* Available signals */}
      <div className="max-h-52 space-y-1 overflow-y-auto pr-1">
        {available.length === 0 && (
          <p className="text-xs text-[var(--text)] opacity-80 py-3 text-center">
            No signals available — sync today's data first.
          </p>
        )}
        {available.map(signal => {
          const odds = signal.bayesian?.best_odd
          if (!odds) return null
          const evPct = signal.bayesian?.ev_pct
          return (
            <button
              key={signal.id}
              type="button"
              onClick={() => addLeg(signal)}
              className="flex w-full items-center justify-between gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-left text-xs transition-colors hover:border-[var(--accent)] hover:bg-[var(--accent-bg)]"
            >
              <div className="flex-1 min-w-0">
                <span className="font-medium text-[var(--text-h)] truncate block">
                  {signal.home_team} vs {signal.away_team}
                </span>
                <span className="text-[var(--text)] opacity-75">
                  {signal.market}
                  {signal.dual_confidence && (
                    <span className={`ml-1.5 font-semibold ${
                      signal.dual_confidence === 'High' ? 'text-green-400' :
                      signal.dual_confidence === 'Medium' ? 'text-yellow-400' : 'text-slate-400'
                    }`}>{signal.dual_confidence}</span>
                  )}
                </span>
              </div>
              <span className="flex shrink-0 items-center gap-2">
                {evPct != null && (
                  <span className={`text-[10px] font-semibold ${evPct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {evPct >= 0 ? '+' : ''}{evPct.toFixed(1)}%
                  </span>
                )}
                <OddsPill odds={odds} />
                <Plus size={11} className="text-[var(--accent)]" />
              </span>
            </button>
          )
        })}
      </div>

      {/* Selected legs */}
      {legs.length > 0 && (
        <div className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--code-bg)] p-3">
          <p className="text-xs font-medium text-[var(--text-h)]">Slip ({legs.length} legs)</p>
          {legs.map(({ signal, odds }) => (
            <div key={signal.id} className="flex items-center gap-2 text-xs">
              <span className="flex-1 truncate text-[var(--text)]">
                {signal.home_team} vs {signal.away_team} — {signal.market}
              </span>
              <OddsPill odds={odds} />
              <button type="button" onClick={() => removeLeg(signal.id)} className="ml-1 text-red-400 hover:text-red-300">
                <Trash2 size={11} />
              </button>
            </div>
          ))}
          <div className="border-t border-[var(--border)] pt-1">
            <CombinedOddsBar odds={combinedOdds} />
          </div>
        </div>
      )}

      {/* Save form */}
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-[var(--text)]">
          <span className="font-medium">Name (optional)</span>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="My custom acca"
            className="rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-sm text-[var(--text-h)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-[var(--text)]">
          <span className="font-medium">Stake (K)</span>
          <input
            type="number" min="0.01" step="0.01"
            value={stake}
            onChange={e => setStake(e.target.value)}
            className="w-24 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-sm text-[var(--text-h)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
        <button
          type="submit"
          disabled={saving || legs.length < 2}
          className="self-end rounded-lg bg-[var(--accent)] px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save Acca'}
        </button>
      </form>
      {saveError && <p className="text-xs text-red-400">{saveError}</p>}
    </div>
  )
}

// ── From AI builder ───────────────────────────────────────────────────────────
// Pools picks from two AI sources into one selectable list:
//   • Recommended Tickets — legs from the Recommended tab's four pre-built slips
//   • AI Advisory top picks — Scout / Strategist / Skeptic picks cross-referenced
//     against today's signals for odds (picks without a matching signal are skipped)
// Saves via confirmRecommendedTicket (card_key: 'custom'), same as From Signals.

const CARD_LABELS = { top_single: 'Top Single', safe: 'Safe', value: 'Value', bold: 'Bold' }

function SourceBadge({ label, emoji }) {
  return (
    <span className="inline-flex items-center gap-0.5 text-[9px] font-semibold px-1.5 py-0.5 rounded bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20 shrink-0">
      {emoji && <span>{emoji}</span>}
      {label}
    </span>
  )
}

function FromAIBuilder({ onCreated, defaultStake = 10 }) {
  const [loadingData, setLoadingData] = useState(true)
  const [fetchError,  setFetchError]  = useState(null)
  const [recData,     setRecData]     = useState(null)
  const [advisorData, setAdvisorData] = useState(null)
  const [signals,     setSignals]     = useState([])
  const [legs,        setLegs]        = useState([])   // [{ key, legData }]
  const [stake,       setStake]       = useState(defaultStake)
  const [name,        setName]        = useState('')
  const [saving,      setSaving]      = useState(false)
  const [saveError,   setSaveError]   = useState(null)
  const today = todayStr()

  useEffect(() => {
    setLoadingData(true)
    setFetchError(null)
    Promise.all([
      fetchRecommendedTickets(today),
      fetchAdvisorInsights(today).catch(() => null),  // Pro-gated — fail silently
      fetchSignals({ date: today, sort_by: 'system' }),
    ])
      .then(([rec, adv, sigs]) => {
        setRecData(rec)
        setAdvisorData(adv)
        setSignals(Array.isArray(sigs) ? sigs : [])
      })
      .catch(e => setFetchError(e.message))
      .finally(() => setLoadingData(false))
  }, []) // eslint-disable-line

  // Build deduplicated pick pool from both sources
  const pooledPicks = useMemo(() => {
    const map = new Map() // `home:away:market` → pick

    // 1. Recommended ticket legs — richest data, processed first
    for (const card of (recData?.cards || [])) {
      const cardLabel = CARD_LABELS[card.key] || card.key
      for (const leg of (card.legs || [])) {
        if (!leg.odds) continue
        const key = `${leg.home_team}:${leg.away_team}:${leg.market}`
        if (!map.has(key)) {
          map.set(key, {
            key,
            home_team: leg.home_team,
            away_team: leg.away_team,
            market:    leg.market,
            odds:      leg.odds,
            ev_pct:    leg.ev_pct ?? null,
            legData:   leg,          // full leg — used directly for saving
            sources:   [],
          })
        }
        map.get(key).sources.push({ label: cardLabel, emoji: '🎟️' })
      }
    }

    // 2. Advisor top picks — cross-reference signals for odds
    for (const advisor of (advisorData?.advisors || [])) {
      for (const pick of (advisor.result?.top_picks || [])) {
        if (typeof pick !== 'object' || !pick.market) continue
        const key = `${pick.home_team}:${pick.away_team}:${pick.market}`

        if (map.has(key)) {
          // Already from recommended — just add the advisor as a source
          map.get(key).sources.push({ label: advisor.name, emoji: advisor.emoji })
        } else {
          // Find matching signal for odds
          const signal = signals.find(s =>
            s.home_team === pick.home_team &&
            s.away_team === pick.away_team &&
            s.market    === pick.market
          )
          if (!signal?.bayesian?.best_odd) continue   // no odds → skip
          map.set(key, {
            key,
            home_team: pick.home_team,
            away_team: pick.away_team,
            market:    pick.market,
            odds:      signal.bayesian.best_odd,
            ev_pct:    signal.bayesian.ev_pct ?? null,
            signal,                  // used via signalToLeg for saving
            sources:   [{ label: advisor.name, emoji: advisor.emoji }],
          })
        }
      }
    }

    return [...map.values()]
  }, [recData, advisorData, signals])

  const legKeys    = new Set(legs.map(l => l.key))
  const available  = pooledPicks.filter(p => !legKeys.has(p.key))
  const combinedOdds = legs.reduce((acc, l) => acc * (l.odds || 1), 1)

  function addLeg(pick) {
    if (legKeys.has(pick.key)) return
    setLegs(prev => [...prev, pick])
  }

  function removeLeg(key) {
    setLegs(prev => prev.filter(l => l.key !== key))
  }

  function pickToLegPayload(pick) {
    if (pick.signal) return signalToLeg(pick.signal, pick.odds)
    // From recommended ticket — pass leg data through directly
    const l = pick.legData
    return {
      signal_id:             l.signal_id ?? null,
      fixture_id:            l.fixture_id,
      match_name:            l.match_name,
      home_team:             l.home_team,
      away_team:             l.away_team,
      league:                l.league ?? null,
      league_tier:           l.league_tier ?? null,
      kickoff_at:            l.kickoff_at ?? null,
      event_date:            l.event_date ?? today,
      market:                l.market,
      selection_name:        l.selection_name || l.market,
      bookmaker:             l.bookmaker ?? null,
      odds:                  l.odds,
      probability:           l.probability ?? null,
      ev_pct:                l.ev_pct ?? null,
      confidence:            l.confidence ?? null,
      agreement:             l.agreement ?? null,
      recommended_stake_pct: l.recommended_stake_pct ?? null,
      source_rule_key:       l.source_rule_key ?? null,
      signal_grade:          l.signal_grade ?? null,
      why_tags:              l.why_tags || [],
    }
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (legs.length < 2) { setSaveError('Select at least 2 legs'); return }
    setSaving(true)
    setSaveError(null)
    try {
      await confirmRecommendedTicket({
        card_key:    'custom',
        stake:       parseFloat(stake),
        ticket_date: today,
        name:        name || undefined,
        legs:        legs.map(pickToLegPayload),
      })
      setLegs([])
      setName('')
      onCreated?.()
    } catch (e) {
      setSaveError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loadingData) {
    return (
      <div className="space-y-2 animate-pulse">
        {[1, 2, 3, 4].map(i => <div key={i} className="h-14 rounded-lg bg-[var(--border)]" />)}
      </div>
    )
  }

  if (fetchError) return <p className="text-xs text-red-400">{fetchError}</p>

  return (
    <div className="space-y-4">
      {/* Pool summary */}
      <p className="text-xs text-[var(--text)] opacity-80">
        {pooledPicks.length} AI-curated pick{pooledPicks.length !== 1 ? 's' : ''} from Recommended Tickets
        {advisorData?.advisors ? ' + AI Advisory (Scout · Strategist · Skeptic)' : ''}.
        Source badges show who recommended each pick.
      </p>

      {/* Available picks */}
      <div className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
        {available.length === 0 && pooledPicks.length === 0 && (
          <p className="text-xs text-[var(--text)] opacity-80 py-3 text-center">
            No AI picks available — run the Advisory and sync today's data first.
          </p>
        )}
        {available.length === 0 && pooledPicks.length > 0 && (
          <p className="text-xs text-[var(--text)] opacity-80 py-3 text-center">
            All picks added to your slip.
          </p>
        )}
        {available.map(pick => (
          <button
            key={pick.key}
            type="button"
            onClick={() => addLeg(pick)}
            className="flex w-full items-start justify-between gap-2 rounded-lg border border-[var(--border)] px-3 py-2.5 text-left text-xs transition-colors hover:border-[var(--accent)] hover:bg-[var(--accent-bg)]"
          >
            <div className="flex-1 min-w-0 space-y-1">
              <div className="font-medium text-[var(--text-h)] truncate">
                {pick.home_team} vs {pick.away_team}
              </div>
              <div className="text-[var(--text)] opacity-75">{pick.market}</div>
              <div className="flex flex-wrap gap-1">
                {pick.sources.map((src, i) => (
                  <SourceBadge key={i} label={src.label} emoji={src.emoji} />
                ))}
              </div>
            </div>
            <span className="flex shrink-0 items-center gap-2 pt-0.5">
              {pick.ev_pct != null && (
                <span className={`text-[10px] font-semibold ${pick.ev_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {pick.ev_pct >= 0 ? '+' : ''}{pick.ev_pct.toFixed(1)}%
                </span>
              )}
              <OddsPill odds={pick.odds} />
              <Plus size={11} className="text-[var(--accent)]" />
            </span>
          </button>
        ))}
      </div>

      {/* Selected legs */}
      {legs.length > 0 && (
        <div className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--code-bg)] p-3">
          <p className="text-xs font-medium text-[var(--text-h)]">Slip ({legs.length} legs)</p>
          {legs.map(pick => (
            <div key={pick.key} className="flex items-center gap-2 text-xs">
              <span className="flex-1 truncate text-[var(--text)]">
                {pick.home_team} vs {pick.away_team} — {pick.market}
              </span>
              <OddsPill odds={pick.odds} />
              <button type="button" onClick={() => removeLeg(pick.key)} className="ml-1 text-red-400 hover:text-red-300">
                <Trash2 size={11} />
              </button>
            </div>
          ))}
          <div className="border-t border-[var(--border)] pt-1">
            <CombinedOddsBar odds={combinedOdds} />
          </div>
        </div>
      )}

      {/* Save form */}
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-[var(--text)]">
          <span className="font-medium">Name (optional)</span>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="My AI acca"
            className="rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-sm text-[var(--text-h)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-[var(--text)]">
          <span className="font-medium">Stake (K)</span>
          <input
            type="number" min="0.01" step="0.01"
            value={stake}
            onChange={e => setStake(e.target.value)}
            className="w-24 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-sm text-[var(--text-h)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
        <button
          type="submit"
          disabled={saving || legs.length < 2}
          className="self-end rounded-lg bg-[var(--accent)] px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save Acca'}
        </button>
      </form>
      {saveError && <p className="text-xs text-red-400">{saveError}</p>}
    </div>
  )
}

// ── From Draft builder ────────────────────────────────────────────────────────
// Pre-populates with legs drafted from the Signals page via the Acca button.
function FromDraftBuilder({ onCreated, defaultStake = 10 }) {
  const { legs: draftLegs, removeLeg } = useAccaDraft()
  const [legs, setLegs]       = useState(() => draftLegs.map(l => ({ ...l })))
  const [stake, setStake]     = useState(defaultStake)
  const [name, setName]       = useState('')
  const [saving, setSaving]   = useState(false)
  const [error, setError]     = useState(null)

  // Sync if user adds/removes from draft while this panel is open
  useEffect(() => { setLegs(draftLegs.map(l => ({ ...l }))) }, [draftLegs])

  const combinedOdds = legs.reduce((acc, l) => acc * (l.odds ?? 1), 1)

  function removeFromSlip(fixtureId) {
    setLegs(prev => prev.filter(l => l.fixture_id !== fixtureId))
    removeLeg(fixtureId)
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (legs.length < 2) { setError('Select at least 2 legs'); return }
    setSaving(true)
    setError(null)
    try {
      const today = todayStr()
      await confirmRecommendedTicket({
        card_key:    'custom',
        stake:       parseFloat(stake),
        ticket_date: today,
        name:        name || undefined,
        legs: legs.map(l => ({
          fixture_id:            l.fixture_id,
          match_name:            `${l.home_team} vs ${l.away_team}`,
          home_team:             l.home_team,
          away_team:             l.away_team,
          league:                l.league ?? null,
          kickoff_at:            l.kickoff_at ?? null,
          event_date:            l.kickoff_at ? l.kickoff_at.slice(0, 10) : today,
          market:                l.market,
          selection_name:        l.market,
          bookmaker:             l.bookmaker ?? null,
          odds:                  l.odds,
          probability:           l.probability ?? null,
          confidence:            l.confidence ?? null,
          agreement:             l.agreement ?? null,
          recommended_stake_pct: null,
          source_rule_key:       null,
          signal_grade:          null,
          why_tags:              [],
        })),
      })
      clearAccaDraft()
      setLegs([])
      setName('')
      onCreated?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (legs.length === 0) {
    return (
      <div className="rounded-lg border border-amber-500/20 bg-amber-500/6 px-6 py-7 text-center">
        <p className="text-sm font-semibold text-amber-400 mb-1.5">Draft is empty</p>
        <p className="text-xs text-slate-400 mb-4">Go to Signals and click <strong className="text-slate-300">Add to Accumulator</strong> on your favourite picks.</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-[var(--text)] opacity-75">
        {legs.length} leg{legs.length !== 1 ? 's' : ''} drafted from the Signals page.
        Click <X size={10} className="inline" /> to remove a leg.
      </p>

      {/* Slip */}
      <div className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--code-bg)] p-3">
        <p className="text-xs font-medium text-[var(--text-h)]">Slip ({legs.length} legs)</p>
        {legs.map(leg => (
          <div key={leg.fixture_id} className="flex items-center gap-2 text-xs">
            <div className="flex-1 min-w-0">
              <span className="font-medium text-[var(--text-h)] truncate block">
                {leg.home_team} vs {leg.away_team}
              </span>
              <span className="text-[var(--text)] opacity-75">{leg.market}</span>
            </div>
            <OddsPill odds={leg.odds} />
            <button type="button" onClick={() => removeFromSlip(leg.fixture_id)} className="ml-1 text-red-400 hover:text-red-300">
              <Trash2 size={11} />
            </button>
          </div>
        ))}
        <div className="border-t border-[var(--border)] pt-1">
          <CombinedOddsBar odds={combinedOdds} />
        </div>
      </div>

      {/* Save form */}
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-[var(--text)]">
          <span className="font-medium">Name (optional)</span>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="My draft acca"
            className="rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-sm text-[var(--text-h)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-[var(--text)]">
          <span className="font-medium">Stake (K)</span>
          <input
            type="number" min="0.01" step="0.01"
            value={stake}
            onChange={e => setStake(e.target.value)}
            className="w-24 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-sm text-[var(--text-h)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
        <button
          type="submit"
          disabled={saving || legs.length < 2}
          className="self-end rounded-lg bg-[var(--accent)] px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save Acca'}
        </button>
      </form>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  )
}


// ── Main export ───────────────────────────────────────────────────────────────
export default function AccumulatorBuilder({ recentBets = [], onCreated, settings }) {
  const { legs: draftLegs } = useAccaDraft()
  const hasDraft = draftLegs.length > 0

  // Default to 'draft' when there are drafted legs, otherwise 'ai'
  const [source, setSource] = useState(() => draftLegs.length > 0 ? 'draft' : 'ai')

  // Switch to draft tab automatically when legs arrive (e.g. user adds from Signals)
  useEffect(() => {
    if (draftLegs.length > 0 && source === 'ai') setSource('draft')
  }, [draftLegs.length]) // eslint-disable-line

  const defaultStake = settings
    ? Math.max(1, Math.round(settings.bankroll * (settings.unitPct / 100)))
    : 10

  const tabs = [
    ...(hasDraft ? [{ id: 'draft', label: `Draft (${draftLegs.length})`, icon: Layers }] : []),
    { id: 'ai', label: 'From AI', icon: Sparkles },
  ]

  return (
    <div className="overflow-hidden rounded-xl border border-cyan-500/20 bg-cyan-500/5 shadow-sm">
      <div className="border-b border-cyan-500/15 px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-[var(--text)] opacity-75">
            Custom Ticket Builder
          </h3>
          <p className="mt-0.5 text-xs text-[var(--text)] opacity-70">
            {hasDraft
              ? `${draftLegs.length} leg${draftLegs.length !== 1 ? 's' : ''} from your draft · or cherry-pick from AI picks`
              : 'Cherry-pick from AI Advisory + Recommended Tickets across all four slips.'}
          </p>
        </div>
        {/* Source tabs */}
        <div className="flex rounded-lg border border-[var(--border)] overflow-hidden text-xs font-semibold">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setSource(id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 transition-colors ${
                source === id
                  ? 'bg-[var(--accent)] text-white'
                  : 'text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'
              }`}
            >
              <Icon size={11} />
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="p-4">
        {source === 'draft'
          ? <FromDraftBuilder onCreated={onCreated} defaultStake={defaultStake} />
          : <FromAIBuilder onCreated={onCreated} defaultStake={defaultStake} />
        }
      </div>
    </div>
  )
}
