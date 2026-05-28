import { useEffect, useState } from 'react'

// Module-level: persists across tab switches (component unmount/remount) within
// the browser session, preventing duplicate auto-tracking when the user navigates
// away from the Tracker page and comes back.
const _autoTrackedThisSession = new Set()
import { CheckCircle, AlertTriangle } from 'lucide-react'
import { useRecommendedTickets } from '../../hooks/useRecommendedTickets'
import { useAuth } from '../../context/AuthContext'
import {
  GeneralTicketCard,
  FreeTicketCard,
  ProTicketCard,
} from './RecommendedTicketCard'
import { confirmRecommendedTicket, trackPick } from '../../api/tracker'

// ── Loading skeleton ──────────────────────────────────────────────────────────
function TicketSkeleton({ lines = 3 }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4 animate-pulse space-y-3">
      <div className="flex items-center gap-2">
        <div className="h-5 w-28 rounded-full bg-[var(--border)]" />
        <div className="h-5 w-10 rounded-full bg-[var(--border)]" />
      </div>
      <div className="h-7 w-24 rounded-lg bg-[var(--border)]" />
      <div className="space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <div key={i} className="h-4 rounded-full bg-[var(--border)]" style={{ width: `${85 - i * 12}%` }} />
        ))}
      </div>
    </div>
  )
}

// ── Result toast ──────────────────────────────────────────────────────────────
function ResultToast({ result, onSwitchTab }) {
  if (!result) return null
  return (
    <div className={`flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-lg border ${
      result.ok
        ? 'text-green-400 bg-green-500/10 border-green-500/20'
        : 'text-red-400 bg-red-500/10 border-red-500/20'
    }`}>
      {result.ok
        ? <CheckCircle size={11} className="shrink-0" />
        : <AlertTriangle size={11} className="shrink-0" />
      }
      <span className="flex-1">{result.msg}</span>
      {result.ok && result.tab && onSwitchTab && (
        <button
          onClick={() => onSwitchTab(result.tab)}
          className="shrink-0 underline underline-offset-2 hover:no-underline capitalize"
        >
          View {result.tab} →
        </button>
      )}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function buildLegPayload(leg) {
  return {
    signal_id:             leg.signal_id,
    fixture_id:            leg.fixture_id,
    match_name:            leg.match_name,
    home_team:             leg.home_team,
    away_team:             leg.away_team,
    league:                leg.league,
    league_tier:           leg.league_tier,
    kickoff_at:            leg.kickoff_at,
    event_date:            leg.event_date,
    market:                leg.market,
    selection_name:        leg.selection_name,
    bookmaker:             leg.bookmaker,
    odds:                  leg.odds,
    probability:           leg.probability,
    ev_pct:                leg.ev_pct,
    confidence:            leg.confidence,
    agreement:             leg.agreement,
    recommended_stake_pct: leg.recommended_stake_pct,
    source_rule_key:       leg.source_rule_key,
    signal_grade:          leg.signal_grade,
    why_tags:              leg.why_tags || [],
  }
}

// ── Main component ────────────────────────────────────────────────────────────
/**
 * RecommendedTicketsTab
 *
 * Renders three named TiTiBet ticket sections:
 *   1. TiTiBet General — all signals, optional manual tracking
 *   2. TiTiBet Free    — 3 deterministic picks, auto-tracked for any auth user
 *   3. TiTiBet Pro     — 4 sub-tickets, auto-tracked for Pro/Elite only
 *
 * Props:
 *   date        — ISO date string (e.g. "2026-05-27")
 *   settings    — { bankroll, unitPct } from settings store
 *   isPro       — boolean (passed from parent page; true if Pro or Elite active)
 *   onSwitchTab — optional (tab: string) => void for "View →" links in toasts
 */
export default function RecommendedTicketsTab({ date, settings, isPro, onSwitchTab }) {
  const { data, loading, error, load } = useRecommendedTickets()
  const { user } = useAuth()
  // Auto-track state
  const [freeAutoTracked,       setFreeAutoTracked]       = useState(false)
  const [proAutoTracked,        setProAutoTracked]        = useState(false)
  const [goalsAccaAutoTracked,  setGoalsAccaAutoTracked]  = useState(false)
  const [freeAutoSaving,        setFreeAutoSaving]        = useState(false)
  const [proAutoSaving,         setProAutoSaving]         = useState(false)

  // Manual save state
  const [generalSaving, setGeneralSaving] = useState(false)
  const [generalResult, setGeneralResult] = useState(null)
  const [proSubSaving,  setProSubSaving]  = useState({}) // { [sub.key]: bool }
  const [proSubResult,  setProSubResult]  = useState({}) // { [sub.key]: result }
  const [proSubLegSaving, setProSubLegSaving] = useState({}) // { [signal_id]: bool }

  // Default stake from settings
  const defaultStake = settings
    ? Math.max(1, Math.round(settings.bankroll * (settings.unitPct / 100)))
    : 100

  // ── Load tickets when date changes ───────────────────────────────────────
  useEffect(() => {
    load(date)
    setFreeAutoTracked(false)
    setProAutoTracked(false)
    setGoalsAccaAutoTracked(false)
    setGeneralResult(null)
    setProSubResult({})
  }, [date]) // eslint-disable-line

  // ── Auto-track Free ticket (any logged-in user) ───────────────────────────
  useEffect(() => {
    if (!data?.free) return
    if (!user) return                          // must be logged in
    if (freeAutoTracked || freeAutoSaving) return
    const key = `free:${date}`
    if (_autoTrackedThisSession.has(key)) return

    const selected = data.free.selected_legs || []
    if (!selected.length) return

    _autoTrackedThisSession.add(key)
    setFreeAutoSaving(true)
    ;(async () => {
      try {
        await confirmRecommendedTicket({
          card_key:    'titibet_free',
          stake:       defaultStake,
          ticket_date: date,
          legs:        selected.map(buildLegPayload),
        })
        setFreeAutoTracked(true)
      } catch {
        // Silently ignore — duplicate bets (already tracked) are OK
        setFreeAutoTracked(true) // treat as tracked so badge shows
      } finally {
        setFreeAutoSaving(false)
      }
    })()
  }, [data?.free, user, date]) // eslint-disable-line

  // ── Auto-track Goals ACCA (any logged-in user) ───────────────────────────
  useEffect(() => {
    if (!data?.pro) return
    if (!user) return
    if (goalsAccaAutoTracked) return
    const key = `goals_acca:${date}`
    if (_autoTrackedThisSession.has(key)) return

    const goalsSub = (data.pro.sub_tickets || []).find(s => s.key === 'goals_acca' && s.legs?.length > 0)
    if (!goalsSub) return

    _autoTrackedThisSession.add(key)
    ;(async () => {
      try {
        await confirmRecommendedTicket({
          card_key:    'titibet_pro_goals_acca',
          stake:       defaultStake,
          ticket_date: date,
          legs:        goalsSub.legs.map(buildLegPayload),
        })
        setGoalsAccaAutoTracked(true)
      } catch {
        setGoalsAccaAutoTracked(true)
      }
    })()
  }, [data?.pro, user, date]) // eslint-disable-line

  // ── Auto-track Pro tickets (Pro / Elite only) ─────────────────────────────
  useEffect(() => {
    if (!data?.pro) return
    if (!isPro) return
    if (proAutoTracked || proAutoSaving) return
    const key = `pro:${date}`
    if (_autoTrackedThisSession.has(key)) return

    const subs = (data.pro.sub_tickets || []).filter(s => s.legs?.length > 0)
    if (!subs.length) return

    _autoTrackedThisSession.add(key)
    setProAutoSaving(true)
    ;(async () => {
      try {
        for (const sub of subs) {
          try {
            await confirmRecommendedTicket({
              card_key:    `titibet_pro_${sub.key}`,
              stake:       defaultStake,
              ticket_date: date,
              legs:        sub.legs.map(buildLegPayload),
            })
          } catch {
            // Continue with remaining subs even if one fails
          }
        }
        setProAutoTracked(true)
      } finally {
        setProAutoSaving(false)
      }
    })()
  }, [data?.pro, isPro, date]) // eslint-disable-line

  // ── Track General as accumulator ──────────────────────────────────────────
  async function handleTrackAllGeneral(legs) {
    setGeneralSaving(true)
    setGeneralResult(null)
    try {
      await confirmRecommendedTicket({
        card_key:    'titibet_general',
        stake:       defaultStake,
        ticket_date: date,
        legs:        legs.map(buildLegPayload),
      })
      setGeneralResult({
        ok: true,
        tab: 'accumulators',
        msg: `TiTiBet General tracked as accumulator (${legs.length} legs).`,
      })
    } catch (e) {
      setGeneralResult({ ok: false, msg: e.message })
    } finally {
      setGeneralSaving(false)
    }
  }

  // ── Track a Best Singles leg individually ─────────────────────────────────
  async function handleTrackProSubLeg(leg) {
    const k = leg.signal_id
    setProSubLegSaving(s => ({ ...s, [k]: true }))
    try {
      await trackPick({
        fixture_id:            leg.fixture_id,
        bookmaker:             leg.bookmaker,
        event_date:            leg.event_date,
        match_name:            leg.match_name,
        league:                leg.league,
        market_type:           leg.market,
        selection_name:        leg.selection_name,
        odds:                  leg.odds,
        stake:                 defaultStake,
        recommended_stake_pct: leg.recommended_stake_pct,
        source_rule_key:       leg.source_rule_key,
        signal_grade:          leg.signal_grade,
        dual_confidence:       leg.confidence,
        dual_agreement:        leg.agreement,
      })
      setProSubResult(r => ({
        ...r,
        best_singles: { ok: true, tab: 'bets', msg: `${leg.match_name} tracked!` },
      }))
    } catch (e) {
      setProSubResult(r => ({
        ...r,
        best_singles: { ok: false, msg: e.message },
      }))
    } finally {
      setProSubLegSaving(s => ({ ...s, [k]: false }))
    }
  }

  // ── Track a Pro sub-ticket manually ──────────────────────────────────────
  async function handleTrackProSub(sub) {
    const k = sub.key
    setProSubSaving(s => ({ ...s, [k]: true }))
    setProSubResult(r => ({ ...r, [k]: null }))
    try {
      await confirmRecommendedTicket({
        card_key:    `titibet_pro_${k}`,
        stake:       defaultStake,
        ticket_date: date,
        legs:        sub.legs.map(buildLegPayload),
      })
      setProSubResult(r => ({
        ...r,
        [k]: { ok: true, tab: 'accumulators', msg: `${sub.label} tracked!` },
      }))
    } catch (e) {
      setProSubResult(r => ({ ...r, [k]: { ok: false, msg: e.message } }))
    } finally {
      setProSubSaving(s => ({ ...s, [k]: false }))
    }
  }

  // ── Manual Free track (fallback if auto-track was skipped/failed) ─────────
  async function handleManualFreeTrack(selected) {
    setFreeAutoSaving(true)
    try {
      await confirmRecommendedTicket({
        card_key:    'titibet_free',
        stake:       defaultStake,
        ticket_date: date,
        legs:        selected.map(buildLegPayload),
      })
      setFreeAutoTracked(true)
    } catch {
      setFreeAutoTracked(true)
    } finally {
      setFreeAutoSaving(false)
    }
  }

  // ── Loading ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="space-y-4">
        <TicketSkeleton lines={4} />
        <TicketSkeleton lines={3} />
        <TicketSkeleton lines={5} />
      </div>
    )
  }

  // ── Error ─────────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
        {error}
      </div>
    )
  }

  // ── No data ───────────────────────────────────────────────────────────────
  if (!data || (!data.general && !data.free && !data.pro)) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-10 flex flex-col items-center gap-2 text-center">
        <span className="text-4xl">🎰</span>
        <p className="text-sm font-semibold text-[var(--text-h)]">No tickets yet for this date</p>
        <p className="text-xs text-[var(--text)] opacity-75 max-w-xs">
          Tickets are built from ranked signals. Sync today&apos;s data first.
        </p>
      </div>
    )
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-5">
      {/* Subtitle */}
      <p className="text-xs text-[var(--text)] opacity-65">
        AI-generated tickets from today&apos;s ranked signals. Default unit stake:{' '}
        <span className="font-semibold text-[var(--text-h)]">K{defaultStake.toLocaleString()}</span>
        {' '}— adjust in Settings.
      </p>

      {/* ── Section 1: TiTiBet General ─────────────────────────────────── */}
      {data.general && (
        <div className="space-y-2">
          <GeneralTicketCard
            ticket={data.general}
            onTrackAll={user ? handleTrackAllGeneral : undefined}
            saving={generalSaving}
          />
          <ResultToast result={generalResult} onSwitchTab={onSwitchTab} />
        </div>
      )}

      {/* ── Section 2: TiTiBet Free ────────────────────────────────────── */}
      {data.free && (
        <div className="space-y-2">
          <FreeTicketCard
            ticket={data.free}
            autoTracked={freeAutoTracked}
            onAutoTrack={user && !freeAutoTracked ? handleManualFreeTrack : undefined}
            saving={freeAutoSaving}
          />
          {!user && (
            <p className="text-xs text-[var(--text)] opacity-70 px-1">
              Sign in to auto-track your free picks.
            </p>
          )}
        </div>
      )}

      {/* ── Section 3: TiTiBet Pro ─────────────────────────────────────── */}
      {data.pro && (
        <div className="space-y-2">
          <ProTicketCard
            ticket={data.pro}
            autoTracked={proAutoTracked}
            autoTrackedKeys={{ goals_acca: goalsAccaAutoTracked }}
            onTrackSub={isPro ? handleTrackProSub : undefined}
            onTrackSubLeg={isPro ? handleTrackProSubLeg : undefined}
            savingKeys={proSubSaving}
            savingLegs={proSubLegSaving}
          />
          {/* Per-sub result toasts */}
          {Object.entries(proSubResult).map(([k, res]) => res && (
            <ResultToast key={k} result={res} onSwitchTab={onSwitchTab} />
          ))}
          {!isPro && (
            <p className="text-xs text-[var(--text)] opacity-70 px-1">
              Upgrade to Pro to auto-track these tickets and unlock sub-ticket details.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
