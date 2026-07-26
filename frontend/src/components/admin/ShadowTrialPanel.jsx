import { useState, useEffect, useCallback } from 'react'
import { FlaskConical, RefreshCw } from 'lucide-react'
import { fetchShadowCandidates } from '../../api/admin'

const OUTCOME_STYLE = {
  Won:     'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  Lost:    'bg-red-500/15 text-red-400 border-red-500/30',
  Pending: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
}

const MARKET_COLOR = {
  'Away Over 0.5': 'text-blue-400 border-blue-500/30',
  'Over 1.5':      'text-violet-400 border-violet-500/30',
  'Over 2.5':      'text-emerald-400 border-emerald-500/30',
}

const PROMOTION_THRESHOLD = 50

function MarketSummaryCard({ s }) {
  const pct = Math.min(100, Math.round((s.n_settled / PROMOTION_THRESHOLD) * 100))
  const marketColor = MARKET_COLOR[s.market] || 'text-[var(--text)] border-[var(--border)]'
  const [textColor] = marketColor.split(' ')
  const barColor = s.promotion_ready ? 'bg-emerald-500' : 'bg-[var(--accent)]'

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <span className={`text-xs font-bold ${textColor}`}>{s.market}</span>
        {s.promotion_ready && (
          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded border bg-emerald-500/15 text-emerald-400 border-emerald-500/30">
            Ready
          </span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <p className="text-[10px] text-[var(--text)] opacity-65 uppercase tracking-wide mb-0.5">Settled</p>
          <p className="text-base font-bold text-[var(--text-h)] tabular-nums">{s.n_settled}</p>
          <p className="text-[10px] text-[var(--text)] opacity-50">of {s.n_total}</p>
        </div>
        <div>
          <p className="text-[10px] text-[var(--text)] opacity-65 uppercase tracking-wide mb-0.5">Win Rate</p>
          <p className={`text-base font-bold tabular-nums ${s.win_rate_pct != null ? (s.win_rate_pct >= 62 ? 'text-emerald-400' : s.win_rate_pct >= 50 ? 'text-amber-400' : 'text-red-400') : 'text-[var(--text)] opacity-40'}`}>
            {s.win_rate_pct != null ? `${s.win_rate_pct}%` : '—'}
          </p>
          <p className="text-[10px] text-[var(--text)] opacity-50">{s.n_wins}W / {s.n_settled - s.n_wins}L</p>
        </div>
        <div>
          <p className="text-[10px] text-[var(--text)] opacity-65 uppercase tracking-wide mb-0.5">Avg Odds</p>
          <p className="text-base font-bold text-[var(--text-h)] tabular-nums">
            {s.avg_odds != null ? s.avg_odds.toFixed(2) : '—'}
          </p>
          <p className="text-[10px] text-[var(--text)] opacity-50">&nbsp;</p>
        </div>
      </div>

      {/* Progress bar toward promotion */}
      <div>
        <div className="flex justify-between items-center mb-1">
          <span className="text-[10px] text-[var(--text)] opacity-65">
            {s.promotion_ready ? 'Promotion threshold reached' : `${s.pending_to_promote} more to promote`}
          </span>
          <span className="text-[10px] text-[var(--text)] opacity-65 tabular-nums">{pct}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
          <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  )
}

export default function ShadowTrialPanel() {
  const [data, setData]           = useState(null)
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState(null)
  const [marketFilter, setMarketFilter] = useState('')
  const [settledOnly, setSettledOnly]   = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetchShadowCandidates({
        market: marketFilter || undefined,
        settledOnly,
      })
      setData(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [marketFilter, settledOnly])

  useEffect(() => { load() }, [load])

  const markets = data?.by_market?.map(s => s.market) ?? []
  const candidates = data?.candidates ?? []

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--border)]">
        <div className="flex items-center gap-2">
          <FlaskConical size={13} className="text-[var(--accent)]" />
          <span className="text-xs font-semibold text-[var(--text-h)]">Shadow Trials</span>
          {data && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] font-semibold">
              {data.total_returned}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={settledOnly}
              onChange={e => setSettledOnly(e.target.checked)}
              className="w-3 h-3 accent-[var(--accent)]"
            />
            <span className="text-[10px] text-[var(--text)] opacity-80">Settled only</span>
          </label>
          <select
            value={marketFilter}
            onChange={e => setMarketFilter(e.target.value)}
            className="text-[10px] bg-[var(--bg)] border border-[var(--border)] rounded px-1.5 py-0.5 text-[var(--text)] focus:outline-none"
          >
            <option value="">All markets</option>
            {markets.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <button
            onClick={load}
            disabled={loading}
            className="text-[var(--text)] opacity-70 hover:opacity-100 transition-opacity disabled:opacity-30"
          >
            <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {error && (
        <p className="px-4 py-3 text-xs text-red-400">{error}</p>
      )}

      {/* Per-market summary cards */}
      {data?.by_market?.length > 0 && (
        <div className="p-4 grid grid-cols-1 sm:grid-cols-3 gap-3 border-b border-[var(--border)]">
          {data.by_market.map(s => <MarketSummaryCard key={s.market} s={s} />)}
        </div>
      )}

      {/* Candidate table */}
      {!loading && candidates.length === 0 && (
        <p className="px-4 py-6 text-xs text-[var(--text)] opacity-70 italic text-center">
          No shadow candidates found.
        </p>
      )}

      {candidates.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--border)] bg-[var(--bg)]">
                <th className="px-3 py-2 text-left font-semibold text-[var(--text)] opacity-65 whitespace-nowrap">Date</th>
                <th className="px-3 py-2 text-left font-semibold text-[var(--text)] opacity-65">Fixture</th>
                <th className="px-3 py-2 text-left font-semibold text-[var(--text)] opacity-65 hidden sm:table-cell">League</th>
                <th className="px-3 py-2 text-left font-semibold text-[var(--text)] opacity-65 whitespace-nowrap">Market</th>
                <th className="px-3 py-2 text-right font-semibold text-[var(--text)] opacity-65 whitespace-nowrap">Prob</th>
                <th className="px-3 py-2 text-right font-semibold text-[var(--text)] opacity-65 whitespace-nowrap">Odds</th>
                <th className="px-3 py-2 text-center font-semibold text-[var(--text)] opacity-65 whitespace-nowrap">Score</th>
                <th className="px-3 py-2 text-center font-semibold text-[var(--text)] opacity-65">Result</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map(c => {
                const mktColor = (MARKET_COLOR[c.market] || '').split(' ')[0] || 'text-[var(--text)]'
                return (
                  <tr key={c.signal_id} className="border-t border-[var(--border)] hover:bg-[var(--bg)] transition-colors">
                    <td className="px-3 py-2 text-[var(--text)] opacity-65 whitespace-nowrap tabular-nums">{c.date}</td>
                    <td className="px-3 py-2">
                      <p className="font-medium text-[var(--text-h)] leading-tight">{c.fixture}</p>
                      <p className="text-[10px] text-[var(--text)] opacity-50 sm:hidden">{c.league}</p>
                    </td>
                    <td className="px-3 py-2 text-[var(--text)] opacity-65 hidden sm:table-cell">
                      <p>{c.league}</p>
                      {c.country && <p className="text-[10px] opacity-60">{c.country}</p>}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <span className={`text-[10px] font-semibold ${mktColor}`}>{c.market}</span>
                    </td>
                    <td className="px-3 py-2 text-right text-[var(--text)] opacity-80 tabular-nums whitespace-nowrap">
                      {c.probability != null ? `${(c.probability * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right font-medium text-[var(--text-h)] tabular-nums whitespace-nowrap">
                      {c.odds != null ? c.odds.toFixed(2) : '—'}
                    </td>
                    <td className="px-3 py-2 text-center text-[var(--text)] opacity-65 tabular-nums whitespace-nowrap">
                      {c.score ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold border ${OUTCOME_STYLE[c.outcome] ?? ''}`}>
                        {c.outcome}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
