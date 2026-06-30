import { useState, useEffect } from 'react'
import { Calendar, RefreshCw, Percent, TrendingUp, AlertCircle } from 'lucide-react'
import { fetchArbOpportunities } from '../api/arb'

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// Tomorrow's fixtures are pre-synced every evening (8pm local), so arb
// opportunities can be checked a day ahead too.
function tomorrowStr() {
  const d = new Date()
  d.setDate(d.getDate() + 1)
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

function ArbCard({ opp, bankroll = 1000 }) {
  const [units, setUnits] = useState(100)
  const stakeA = (opp.stake_a / 100 * units).toFixed(2)
  const stakeB = (opp.stake_b / 100 * units).toFixed(2)
  const profit  = (opp.arb_pct / 100 * units).toFixed(2)

  const tier = opp.arb_pct >= 3 ? 'high' : opp.arb_pct >= 1.5 ? 'mid' : 'low'
  const tierStyle = {
    high: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    mid:  'bg-amber-500/15  text-amber-400  border-amber-500/30',
    low:  'bg-blue-500/15   text-blue-400   border-blue-500/30',
  }[tier]

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden hover:border-[var(--accent)]/40 transition-colors">
      {/* Top stripe */}
      <div className={`h-1 w-full ${tier === 'high' ? 'bg-emerald-500' : tier === 'mid' ? 'bg-amber-400' : 'bg-blue-400'}`} />

      <div className="p-4 space-y-3">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] text-[var(--text)] opacity-70 mb-0.5">
              {opp.country} · {opp.league}
              {opp.kickoff_at && <span className="ml-1">· {fmtKickoff(opp.kickoff_at)}</span>}
            </p>
            <p className="text-sm font-semibold text-[var(--text-h)] leading-tight">
              {opp.home_team} vs {opp.away_team}
            </p>
            <p className="text-xs text-[var(--text)] opacity-80 mt-0.5">{opp.market_type}</p>
          </div>
          <div className={`shrink-0 rounded-lg border px-3 py-1.5 text-center ${tierStyle}`}>
            <p className="text-lg font-black tabular-nums leading-none">+{opp.arb_pct}%</p>
            <p className="text-[9px] mt-0.5 opacity-70">arb margin</p>
          </div>
        </div>

        {/* Sides */}
        <div className="grid grid-cols-2 gap-2">
          {[
            { side: opp.side_a, odds: opp.odds_a, bookie: opp.bookie_a, stake: stakeA },
            { side: opp.side_b, odds: opp.odds_b, bookie: opp.bookie_b, stake: stakeB },
          ].map(({ side, odds, bookie, stake }) => (
            <div key={side} className="rounded-lg bg-[var(--code-bg)] p-2.5 space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-[var(--text-h)]">{side}</span>
                <span className="text-sm font-black font-mono text-[var(--accent)]">{odds.toFixed(2)}</span>
              </div>
              <p className="text-[10px] text-[var(--text)] opacity-55">{bookie}</p>
              <p className="text-xs font-semibold text-[var(--text-h)]">Stake: <span className="font-mono">{stake}</span></p>
            </div>
          ))}
        </div>

        {/* Stake calculator */}
        <div className="flex items-center gap-3 pt-1 border-t border-[var(--border)]">
          <div className="flex items-center gap-1.5 flex-1">
            <label className="text-[10px] text-[var(--text)] opacity-55 whitespace-nowrap">Total stake:</label>
            <input
              type="number"
              min={10}
              step={10}
              value={units}
              onChange={e => setUnits(Math.max(1, parseFloat(e.target.value) || 1))}
              className="w-20 px-2 py-0.5 rounded border border-[var(--border)] bg-[var(--bg)] text-xs text-[var(--text-h)] font-mono focus:outline-none focus:border-[var(--accent)]"
            />
          </div>
          <div className="text-right">
            <p className="text-[10px] text-[var(--text)] opacity-55">Guaranteed profit</p>
            <p className="text-sm font-bold text-emerald-400 tabular-nums">+{profit}</p>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function ArbPage() {
  const today = todayStr()
  const maxDate = tomorrowStr()
  const [date, setDate] = useState(today)
  const [opps, setOpps] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  function load(d) {
    setLoading(true)
    setError(null)
    fetchArbOpportunities(d)
      .then(data => setOpps(Array.isArray(data) ? data : []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load(date) }, [date])

  return (
    <div className="space-y-6 pb-24 lg:pb-8">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Percent size={18} className="text-[var(--accent)]" />
          <h1 className="text-xl font-bold text-[var(--text-h)]">Arbitrage</h1>
        </div>
        <p className="text-sm text-[var(--text)] opacity-70 max-w-xl">
          Two-way markets where the combined implied probability across bookmakers is below 100% —
          backing both sides guarantees a profit regardless of outcome.
        </p>
      </div>

      {/* How it works */}
      <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 flex gap-3">
        <AlertCircle size={16} className="text-amber-400 shrink-0 mt-0.5" />
        <div className="text-xs text-[var(--text)] opacity-80 space-y-1">
          <p><span className="font-semibold text-amber-400">How arb works:</span> Bet Side A at Bookie A and Side B at Bookie B using the exact stakes shown. The market margin gap is your guaranteed profit — set the total stake and the profit scales linearly.</p>
          <p className="opacity-80">Opportunities &gt;3% are rare and close fast. Most arb here is 0.3–1.5% — small but risk-free. Act quickly; bookmakers update odds within minutes.</p>
        </div>
      </div>

      {/* Date picker */}
      <div className="flex items-center gap-2">
        <div
          className="relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] cursor-pointer hover:bg-[var(--code-bg)] transition-colors"
          onClick={() => document.getElementById('arb-date-input')?.showPicker()}
        >
          <Calendar size={13} className="text-[var(--accent)] shrink-0" />
          <span className="text-sm font-medium text-[var(--text-h)]">
            {fmtDate(date)}{date === today ? ' · Today' : date === maxDate ? ' · Tomorrow' : ''}
          </span>
          <input
            id="arb-date-input"
            type="date"
            value={date}
            max={maxDate}
            onChange={e => e.target.value && setDate(e.target.value)}
            style={{ position: 'absolute', opacity: 0, pointerEvents: 'none', width: 0, height: 0 }}
          />
        </div>
        {loading && <RefreshCw size={13} className="animate-spin text-[var(--text)] opacity-65" />}
        {!loading && (
          <button
            onClick={() => load(date)}
            className="flex items-center gap-1 text-xs text-[var(--text)] opacity-70 hover:opacity-100 transition-opacity"
          >
            <RefreshCw size={12} /> Refresh
          </button>
        )}
      </div>

      {/* Summary bar */}
      {!loading && !error && opps.length > 0 && (
        <div className="flex items-center gap-4 text-xs text-[var(--text)] opacity-70">
          <span><span className="font-semibold text-[var(--text-h)]">{opps.length}</span> opportunit{opps.length !== 1 ? 'ies' : 'y'}</span>
          <span>Best: <span className="font-semibold text-emerald-400">+{opps[0].arb_pct}%</span></span>
          <span className="ml-auto">Sorted by margin</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">{error}</div>
      )}

      {/* Loading */}
      {loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 animate-pulse">
          {[1, 2, 3, 4].map(i => <div key={i} className="h-52 rounded-xl bg-[var(--border)]" />)}
        </div>
      )}

      {/* Empty */}
      {!loading && !error && opps.length === 0 && (
        <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-12 flex flex-col items-center gap-3 text-center">
          <TrendingUp size={32} className="text-[var(--text)] opacity-20" />
          <p className="text-sm font-semibold text-[var(--text-h)]">No arbitrage opportunities today</p>
          <p className="text-xs text-[var(--text)] opacity-80 max-w-xs">
            Markets are efficient — arb windows open and close within minutes. Check back after the next odds sync, or try a different date.
          </p>
        </div>
      )}

      {/* Cards */}
      {!loading && opps.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {opps.map((opp, i) => (
            <ArbCard key={i} opp={opp} />
          ))}
        </div>
      )}
    </div>
  )
}
