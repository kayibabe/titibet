import { useState, useEffect } from 'react'
import { ArrowLeft } from 'lucide-react'
import { fetchFixtureSignals, fetchMatchInfo, fetchOddsMatrix } from '../api/signals'
import MarketRow from '../components/signals/MarketRow'
import TrackModal from '../components/tracker/TrackModal'
import LoadingSpinner from '../components/shared/LoadingSpinner'
import ContradictionAlert from '../components/signals/ContradictionAlert'

// ── Small reusable pieces ─────────────────────────────────────────────────────

function Tab({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap ${
        active
          ? 'border-[var(--accent)] text-[var(--accent)]'
          : 'border-transparent text-[var(--text)] hover:text-[var(--text-h)]'
      }`}
    >
      {label}
    </button>
  )
}

function FormBadge({ result }) {
  const colors = { W: 'bg-green-500 text-white', D: 'bg-yellow-500 text-white', L: 'bg-red-500 text-white' }
  return (
    <span className={`inline-flex items-center justify-center w-6 h-6 rounded text-xs font-bold ${colors[result] || 'bg-[var(--code-bg)] text-[var(--text)]'}`}>
      {result}
    </span>
  )
}

function StatBar({ label, homeVal, awayVal, homeRaw, awayRaw, invert = false }) {
  const total = (homeRaw || 0) + (awayRaw || 0)
  const homePct = total > 0 ? (homeRaw / total) * 100 : 50
  const awayPct = 100 - homePct
  const homeWins = invert ? homePct < awayPct : homePct >= awayPct
  return (
    <div className="grid grid-cols-[72px_1fr_72px] sm:grid-cols-[90px_1fr_90px] items-center gap-2 py-2">
      <span className={`text-xs sm:text-sm font-bold text-right tabular-nums ${homeWins ? 'text-[var(--text-h)]' : 'text-[var(--text)] opacity-75'}`}>
        {homeVal}
      </span>
      <div className="flex items-center gap-1.5">
        <div className="flex-1 h-2 rounded-full overflow-hidden bg-[var(--code-bg)] flex">
          <div
            className={`h-full rounded-full transition-all ${homeWins ? 'bg-green-500' : 'bg-[var(--text)] opacity-70'}`}
            style={{ width: `${homePct}%` }}
          />
        </div>
        <span className="text-[9px] sm:text-[10px] text-[var(--text)] opacity-55 w-16 sm:w-20 text-center shrink-0 leading-tight">{label}</span>
        <div className="flex-1 h-2 rounded-full overflow-hidden bg-[var(--code-bg)] flex justify-end">
          <div
            className={`h-full rounded-full transition-all ${!homeWins ? 'bg-green-500' : 'bg-[var(--text)] opacity-70'}`}
            style={{ width: `${awayPct}%` }}
          />
        </div>
      </div>
      <span className={`text-xs sm:text-sm font-bold text-left tabular-nums ${!homeWins ? 'text-[var(--text-h)]' : 'text-[var(--text)] opacity-75'}`}>
        {awayVal}
      </span>
    </div>
  )
}

function ProbBar({ market, prob, confidence, isValue, bestOdd, bookmaker }) {
  const confColor = {
    High:   'bg-green-500',
    Medium: 'bg-yellow-500',
    Low:    'bg-slate-500',
  }[confidence] || 'bg-[var(--code-bg)]'

  const barColor =
    prob >= 70 ? 'bg-green-500' :
    prob >= 50 ? 'bg-yellow-500' :
    prob >= 35 ? 'bg-orange-500' :
    'bg-red-500'

  return (
    <div className="py-2 border-b border-[var(--border)] last:border-0">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-[var(--text-h)] font-medium">{market}</span>
          {isValue && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold text-white ${confColor}`}>
              {confidence}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {bestOdd && (
            <span className="text-xs text-[var(--text)] opacity-85 hidden sm:inline">
              {bookmaker} <span className="font-mono text-[var(--accent)]">{bestOdd.toFixed(2)}</span>
            </span>
          )}
          <span className="text-sm font-bold font-mono text-[var(--text-h)] w-12 text-right">{prob}%</span>
        </div>
      </div>
      <div className="h-2 bg-[var(--code-bg)] rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${Math.min(prob, 100)}%` }} />
      </div>
    </div>
  )
}

function HighlightList({ highlights }) {
  if (!highlights?.length) return <p className="text-xs text-[var(--text)] opacity-65 py-2">Not enough historical data.</p>
  return (
    <ul className="space-y-2">
      {highlights.map((h, i) => (
        <li key={i} className="text-sm text-[var(--text)] leading-snug"
          dangerouslySetInnerHTML={{ __html: h.replace(/\*\*(.*?)\*\*/g, '<strong class="text-[var(--text-h)]">$1</strong>') }}
        />
      ))}
    </ul>
  )
}

function H2HRow({ match, homeTeam }) {
  const homeWon  = match.home_score > match.away_score
  const awayWon  = match.away_score > match.home_score
  const isHome   = match.home_team === homeTeam
  const weWon    = (isHome && homeWon) || (!isHome && awayWon)
  const drew     = match.home_score === match.away_score
  const resultBg  = weWon ? 'bg-green-500/15 border-green-500/30' : drew ? 'bg-yellow-500/10 border-yellow-500/30' : 'bg-red-500/10 border-red-500/30'
  const resultTxt = weWon ? 'text-green-400' : drew ? 'text-yellow-400' : 'text-red-400'
  const d = match.date ? new Date(match.date + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—'

  return (
    <div className={`flex items-center justify-between px-3 sm:px-4 py-3 rounded-lg border ${resultBg} mb-2`}>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-[var(--text-h)] truncate">
          {match.home_team} <span className="text-[var(--text)] opacity-65">vs</span> {match.away_team}
        </div>
        <div className="text-xs text-[var(--text)] opacity-75 mt-0.5">{d}</div>
      </div>
      <div className={`text-lg font-bold font-mono shrink-0 ml-3 ${resultTxt}`}>
        {match.home_score} – {match.away_score}
      </div>
    </div>
  )
}

// Mobile signal card — shown instead of table row on small screens
function SignalCard({ sig, onTrack }) {
  const conf = sig.dual_confidence || sig.bayesian?.confidence
  const confColor = { High: 'text-green-400', Medium: 'text-yellow-400', Low: 'text-slate-400' }[conf] || 'text-[var(--text)]'

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-semibold text-[var(--text-h)]">{sig.market}</span>
        {conf && <span className={`text-xs font-bold shrink-0 ${confColor}`}>{conf}</span>}
      </div>
      <div className="flex items-center gap-3 text-xs text-[var(--text)] opacity-80 flex-wrap">
        {sig.bayesian?.best_odd && (
          <span>Best: <span className="font-mono text-[var(--text-h)]">{sig.bayesian.best_odd.toFixed(2)}</span></span>
        )}
        {sig.dual_agreement && <span>{sig.dual_agreement}</span>}
        {sig.quality_score != null && <span>QS {sig.quality_score.toFixed(2)}</span>}
      </div>
      {onTrack && (
        <button
          onClick={() => onTrack(sig)}
          className="text-xs text-[var(--accent)] font-semibold hover:underline"
        >
          Track Pick →
        </button>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

// ── Odds Comparison Tab ───────────────────────────────────────────────────────
function OddsMatrixTab({ loading, matrix }) {
  if (loading) return <div className="py-12 flex justify-center"><LoadingSpinner /></div>
  if (!matrix || matrix.rows.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-12 text-center">
        <p className="text-sm text-[var(--text)] opacity-80">No bookmaker odds available for this fixture.</p>
      </div>
    )
  }

  const { bookmakers, rows } = matrix
  const sharp = new Set(['Pinnacle', 'Bet365'])

  // Group rows by market_type
  const grouped = {}
  for (const row of rows) {
    if (!grouped[row.market_type]) grouped[row.market_type] = []
    grouped[row.market_type].push(row)
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-[var(--text)] opacity-55">
        Best odds highlighted per row. Sharp books (Pinnacle, Bet365) are used for EV reference. Line-shop against the others.
      </p>
      {Object.entries(grouped).map(([marketType, mRows]) => (
        <div key={marketType} className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden">
          <div className="px-4 py-2.5 bg-[var(--code-bg)] border-b border-[var(--border)]">
            <p className="text-xs font-semibold text-[var(--text-h)]">{marketType}</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--border)]">
                  <th className="px-3 py-2 text-left text-[var(--text)] opacity-80 font-medium whitespace-nowrap">Selection</th>
                  {bookmakers.map(bk => (
                    <th key={bk} className={`px-3 py-2 text-right font-medium whitespace-nowrap ${sharp.has(bk) ? 'text-amber-400' : 'text-[var(--text)] opacity-80'}`}>
                      {bk}{sharp.has(bk) ? ' ★' : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {mRows.map((row, i) => {
                  const validOdds = bookmakers.map(bk => row.odds[bk]).filter(Boolean)
                  const maxOdd = validOdds.length ? Math.max(...validOdds) : null
                  return (
                    <tr key={i} className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--code-bg)] transition-colors">
                      <td className="px-3 py-2.5 font-medium text-[var(--text-h)] whitespace-nowrap">{row.selection}</td>
                      {bookmakers.map(bk => {
                        const odd = row.odds[bk]
                        const isBest = odd && odd === maxOdd
                        return (
                          <td key={bk} className="px-3 py-2.5 text-right tabular-nums whitespace-nowrap">
                            {odd ? (
                              <span className={`font-mono font-semibold ${isBest ? 'text-emerald-400' : 'text-[var(--text)]'}`}>
                                {odd.toFixed(2)}
                                {isBest && <span className="ml-1 text-[9px] text-emerald-400 opacity-70">best</span>}
                              </span>
                            ) : (
                              <span className="text-[var(--text)] opacity-25">—</span>
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  )
}

export default function DeepDivePage({ fixtureId, settings, onBack }) {
  const [signals,     setSignals]     = useState([])
  const [matchInfo,   setMatchInfo]   = useState(null)
  const [oddsMatrix,  setOddsMatrix]  = useState(null)
  const [oddsLoading, setOddsLoading] = useState(false)
  const [loading,     setLoading]     = useState(true)
  const [infoLoading, setInfoLoading] = useState(true)
  const [error,       setError]       = useState(null)
  const [trackingSignal, setTrackingSignal] = useState(null)
  const [activeTab,   setActiveTab]   = useState('overview')

  useEffect(() => {
    if (!fixtureId) return
    setLoading(true)
    setInfoLoading(true)

    fetchFixtureSignals(fixtureId)
      .then(data => setSignals(Array.isArray(data) ? data : []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))

    fetchMatchInfo(fixtureId)
      .then(data => setMatchInfo(data))
      .catch(() => setMatchInfo(null))
      .finally(() => setInfoLoading(false))
  }, [fixtureId])

  if (loading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
  if (error)   return <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">{error}</div>

  const first   = signals[0] || {}
  const fixture = matchInfo?.fixture || {
    home_team:   first.home_team,
    away_team:   first.away_team,
    league:      first.league,
    league_tier: first.league_tier,
    status:      first.status,
    home_score:  first.home_score,
    away_score:  first.away_score,
    kickoff_at:  first.kickoff_at,
  }
  const homeTeam = fixture.home_team || '—'
  const awayTeam = fixture.away_team || '—'
  const kickoffStr = fixture.kickoff_at
    ? (() => {
        const utc = fixture.kickoff_at.endsWith('Z') || fixture.kickoff_at.includes('+') ? fixture.kickoff_at : fixture.kickoff_at + 'Z'
        return new Date(utc).toLocaleString([], { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true })
      })()
    : fixture.event_date || ''
  const isFinished    = ['FT', 'AET', 'PEN'].includes(fixture.status)
  const contradictions = signals.filter(s => s.dual_agreement === 'Contradiction')

  const hs  = matchInfo?.home_stats
  const as_ = matchInfo?.away_stats

  const TABS = [
    { id: 'overview',     label: 'Overview' },
    { id: 'stats',        label: 'Stats' },
    { id: 'probability',  label: 'Probability' },
    { id: 'h2h',          label: 'H2H' },
    { id: 'signals',      label: 'Signals' },
    { id: 'odds',         label: 'Odds Comparison' },
  ]

  return (
    <div className="space-y-5">
      {/* Back */}
      <button onClick={onBack} className="flex items-center gap-1.5 text-sm text-[var(--text)] hover:text-[var(--accent)] transition-colors">
        <ArrowLeft size={15} /> Back
      </button>

      {/* Fixture header */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4 sm:p-5">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg sm:text-xl font-bold text-[var(--text-h)] leading-snug">
              {homeTeam} <span className="text-[var(--text)] opacity-70 font-normal">vs</span> {awayTeam}
            </h2>
            <p className="text-xs sm:text-sm text-[var(--text)] opacity-75 mt-0.5">
              {fixture.league}{kickoffStr ? ` · ${kickoffStr}` : ''}{fixture.league_tier ? ` · Tier ${fixture.league_tier}` : ''}
            </p>
          </div>
          {isFinished ? (
            <div className="text-2xl sm:text-3xl font-bold font-mono text-[var(--text-h)] shrink-0">
              {fixture.home_score} – {fixture.away_score}
            </div>
          ) : fixture.status ? (
            <span className="text-xs px-2 py-1 rounded-full border border-[var(--accent)] text-[var(--accent)] font-medium shrink-0">
              {fixture.status}
            </span>
          ) : null}
        </div>

        {/* Form pills */}
        {hs && as_ && (
          <div className="flex items-center gap-4 mt-4 flex-wrap">
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-[var(--text)] opacity-80 mr-1 uppercase tracking-wide">Form</span>
              {hs.form.map((r, i) => <FormBadge key={i} result={r} />)}
            </div>
            <span className="text-xs text-[var(--text)] opacity-65">vs</span>
            <div className="flex items-center gap-1">
              {as_.form.map((r, i) => <FormBadge key={i} result={r} />)}
            </div>
          </div>
        )}
      </div>

      {contradictions.length > 0 && <ContradictionAlert mixedSignals={contradictions.map(s => s.market)} />}

      {/* Tabs — scrollable on mobile */}
      <div className="flex gap-0.5 border-b border-[var(--border)] overflow-x-auto scrollbar-none">
        {TABS.map(t => (
          <Tab
            key={t.id}
            label={t.label}
            active={activeTab === t.id}
            onClick={() => {
              setActiveTab(t.id)
              if (t.id === 'odds' && !oddsMatrix && !oddsLoading) {
                setOddsLoading(true)
                fetchOddsMatrix(fixtureId)
                  .then(d => setOddsMatrix(d))
                  .catch(() => setOddsMatrix({ bookmakers: [], rows: [] }))
                  .finally(() => setOddsLoading(false))
              }
            }}
          />
        ))}
      </div>

      {/* ── OVERVIEW ──────────────────────────────────────────────────────── */}
      {activeTab === 'overview' && (
        <div className="grid sm:grid-cols-2 gap-4">
          {[
            { team: homeTeam, highlights: matchInfo?.home_highlights },
            { team: awayTeam, highlights: matchInfo?.away_highlights },
          ].map(({ team, highlights }) => (
            <div key={team} className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4 sm:p-5">
              <p className="text-sm font-semibold text-[var(--text-h)] mb-3">⚽ {team}</p>
              {infoLoading ? <LoadingSpinner /> : <HighlightList highlights={highlights} />}
            </div>
          ))}
        </div>
      )}

      {/* ── STATS ─────────────────────────────────────────────────────────── */}
      {activeTab === 'stats' && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4 sm:p-5">
          {infoLoading ? <LoadingSpinner /> : !hs ? (
            <p className="text-sm text-[var(--text)] opacity-75 py-4 text-center">No historical stats available yet.</p>
          ) : (
            <>
              <div className="grid grid-cols-[72px_1fr_72px] sm:grid-cols-[90px_1fr_90px] gap-2 mb-1">
                <span className="text-xs font-semibold text-[var(--accent)] text-right truncate">{homeTeam}</span>
                <span />
                <span className="text-xs font-semibold text-[var(--accent)] text-left truncate">{awayTeam}</span>
              </div>
              <StatBar label="PLAYED"       homeVal={hs.played}           awayVal={as_.played}           homeRaw={hs.played}            awayRaw={as_.played}/>
              <StatBar label="WIN %"        homeVal={`${hs.win_pct}%`}    awayVal={`${as_.win_pct}%`}    homeRaw={hs.win_pct}           awayRaw={as_.win_pct}/>
              <StatBar label="DRAW %"       homeVal={`${hs.draw_pct}%`}   awayVal={`${as_.draw_pct}%`}   homeRaw={hs.draw_pct}          awayRaw={as_.draw_pct}/>
              <StatBar label="LOST %"       homeVal={`${hs.loss_pct}%`}   awayVal={`${as_.loss_pct}%`}   homeRaw={hs.loss_pct}          awayRaw={as_.loss_pct} invert/>
              <StatBar label="GOAL DIFF"    homeVal={hs.goal_difference}  awayVal={as_.goal_difference}  homeRaw={Math.max(0,hs.goal_difference+10)}  awayRaw={Math.max(0,as_.goal_difference+10)}/>
              <StatBar label="AVG FOR"      homeVal={hs.avg_goals_for}    awayVal={as_.avg_goals_for}    homeRaw={hs.avg_goals_for}     awayRaw={as_.avg_goals_for}/>
              <StatBar label="AVG AGAINST"  homeVal={hs.avg_goals_against} awayVal={as_.avg_goals_against} homeRaw={hs.avg_goals_against} awayRaw={as_.avg_goals_against} invert/>
              <StatBar label="PPG"          homeVal={hs.ppg}              awayVal={as_.ppg}              homeRaw={hs.ppg}               awayRaw={as_.ppg}/>
              <p className="text-[10px] text-[var(--text)] opacity-55 mt-3 text-center">
                Last {hs.played} ({homeTeam}) · {as_.played} ({awayTeam}) completed matches
              </p>
            </>
          )}
        </div>
      )}

      {/* ── PROBABILITY ───────────────────────────────────────────────────── */}
      {activeTab === 'probability' && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4 sm:p-5">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">🤖</span>
            <span className="text-sm font-semibold text-[var(--text-h)]">Predictions — Intelligence Platform</span>
          </div>
          {infoLoading ? <LoadingSpinner /> : !matchInfo?.probabilities?.length ? (
            <p className="text-sm text-[var(--text)] opacity-75 py-4 text-center">No probabilities computed for this fixture.</p>
          ) : (
            <div>
              {matchInfo.probabilities.map(p => (
                <ProbBar
                  key={p.market}
                  market={p.market}
                  prob={p.prob}
                  confidence={p.confidence}
                  isValue={p.is_value}
                  bestOdd={p.best_odd}
                  bookmaker={p.bookmaker}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── H2H ───────────────────────────────────────────────────────────── */}
      {activeTab === 'h2h' && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4 sm:p-5">
          <h3 className="text-sm font-semibold text-[var(--text-h)] mb-4">
            Head-to-Head
          </h3>
          {infoLoading ? <LoadingSpinner /> : !matchInfo?.h2h?.length ? (
            <p className="text-sm text-[var(--text)] opacity-75 py-4 text-center">No previous meetings found.</p>
          ) : (
            <>
              {(() => {
                const hw = matchInfo.h2h.filter(m => {
                  const hWon = m.home_score > m.away_score
                  return (m.home_team === homeTeam && hWon) || (m.away_team === homeTeam && !hWon && m.home_score !== m.away_score)
                }).length
                const aw = matchInfo.h2h.filter(m => {
                  const aWon = m.away_score > m.home_score
                  return (m.away_team === awayTeam && aWon) || (m.home_team === awayTeam && !aWon && m.home_score !== m.away_score)
                }).length
                const draws = matchInfo.h2h.filter(m => m.home_score === m.away_score).length
                return (
                  <div className="grid grid-cols-3 py-3 mb-4 rounded-lg bg-[var(--code-bg)] text-center">
                    <div><div className="text-xl sm:text-2xl font-bold text-green-400">{hw}</div><div className="text-[10px] sm:text-xs text-[var(--text)] opacity-75 mt-0.5 truncate px-1">{homeTeam} wins</div></div>
                    <div><div className="text-xl sm:text-2xl font-bold text-yellow-400">{draws}</div><div className="text-[10px] sm:text-xs text-[var(--text)] opacity-75 mt-0.5">Draws</div></div>
                    <div><div className="text-xl sm:text-2xl font-bold text-red-400">{aw}</div><div className="text-[10px] sm:text-xs text-[var(--text)] opacity-75 mt-0.5 truncate px-1">{awayTeam} wins</div></div>
                  </div>
                )
              })()}
              {matchInfo.h2h.map((m, i) => <H2HRow key={i} match={m} homeTeam={homeTeam} />)}
            </>
          )}
        </div>
      )}

      {/* ── SIGNALS ───────────────────────────────────────────────────────── */}
      {activeTab === 'signals' && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4 sm:p-5">
          <h3 className="text-sm font-semibold text-[var(--text-h)] mb-3">All Markets — Full Analysis</h3>
          {signals.length === 0 ? (
            <p className="text-center py-8 text-[var(--text)] opacity-75">No signals for this fixture.</p>
          ) : (
            <>
              {/* Mobile: card list */}
              <div className="sm:hidden space-y-3">
                {signals.map(sig => (
                  <SignalCard key={sig.id} sig={sig} onTrack={setTrackingSignal} />
                ))}
              </div>
              {/* Desktop: table */}
              <div className="hidden sm:block overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-[var(--text)] opacity-85 border-b border-[var(--border)]">
                      <th className="px-3 py-2 text-left">Market</th>
                      <th className="px-3 py-2 text-left">Confidence</th>
                      <th className="px-3 py-2 text-left">Agreement</th>
                      <th className="px-3 py-2 text-left">Fair → Offered</th>
                      <th className="px-3 py-2 text-right">Quality</th>
                      <th className="px-3 py-2 text-right">Stake</th>
                      <th className="px-3 py-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {signals.map(sig => (
                      <MarketRow
                        key={sig.id}
                        signal={sig}
                        onTrack={picked => setTrackingSignal({ ...picked, tracking_source_family: 'Deep Dive' })}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── ODDS COMPARISON ───────────────────────────────────────────── */}
      {activeTab === 'odds' && (
        <OddsMatrixTab
          fixtureId={fixtureId}
          matrix={oddsMatrix}
          loading={oddsLoading}
          onLoad={() => {
            if (oddsMatrix || oddsLoading) return
            setOddsLoading(true)
            fetchOddsMatrix(fixtureId)
              .then(d => setOddsMatrix(d))
              .catch(() => setOddsMatrix({ bookmakers: [], rows: [] }))
              .finally(() => setOddsLoading(false))
          }}
        />
      )}

      {trackingSignal && (
        <TrackModal
          signal={trackingSignal}
          bankroll={settings?.bankroll}
          onClose={() => setTrackingSignal(null)}
          onTracked={() => setTrackingSignal(null)}
        />
      )}
    </div>
  )
}
