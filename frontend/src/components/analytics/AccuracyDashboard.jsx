import { useState, useEffect } from 'react'
import { fetchSignalAccuracy } from '../../api/analytics'

const LOOKBACK_OPTIONS = [
  { label: '30d', value: 30 },
  { label: '90d', value: 90 },
  { label: '180d', value: 180 },
  { label: 'All', value: 730 },
]

function hitColor(rate) {
  if (rate == null) return 'text-[var(--text)] opacity-50'
  if (rate >= 65) return 'text-emerald-400'
  if (rate >= 55) return 'text-amber-400'
  return 'text-red-400'
}

function HitBar({ rate, maxRate = 100 }) {
  const pct = Math.max(0, Math.min(100, (rate / maxRate) * 100))
  const color = rate >= 65 ? 'bg-emerald-500' : rate >= 55 ? 'bg-amber-400' : 'bg-red-400'
  return (
    <div className="w-full h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
      <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

function OverallStat({ label, value, sub }) {
  return (
    <div className="flex flex-col items-center gap-0.5 px-4 py-3 rounded-lg border border-[var(--border)] bg-[var(--code-bg)] min-w-[90px]">
      <span className="text-xl font-bold text-[var(--text-h)] tabular-nums">{value}</span>
      <span className="text-[10px] text-[var(--text)] opacity-60 text-center whitespace-nowrap">{label}</span>
      {sub && <span className="text-[9px] text-[var(--text)] opacity-40">{sub}</span>}
    </div>
  )
}

export default function AccuracyDashboard() {
  const [lookback, setLookback] = useState(90)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchSignalAccuracy(lookback)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [lookback])

  return (
    <div className="space-y-5">
      {/* Period selector */}
      <div className="flex items-center gap-1">
        {LOOKBACK_OPTIONS.map(opt => (
          <button
            key={opt.value}
            onClick={() => setLookback(opt.value)}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              lookback === opt.value
                ? 'bg-[var(--accent)] text-white'
                : 'bg-[var(--code-bg)] text-[var(--text)] border border-[var(--border)] hover:border-[var(--accent)]'
            }`}
          >
            {opt.label}
          </button>
        ))}
        <span className="ml-2 text-[10px] text-[var(--text)] opacity-40">all generated signals, not just tracked bets</span>
      </div>

      {loading && (
        <p className="text-xs text-[var(--text)] opacity-50 py-4 text-center">Loading…</p>
      )}
      {error && (
        <p className="text-xs text-red-400 py-2">{error}</p>
      )}

      {data && !loading && (
        <>
          {/* Overall KPIs */}
          <div className="flex flex-wrap gap-3">
            <OverallStat
              label="Hit Rate"
              value={data.hit_rate != null ? `${data.hit_rate}%` : '—'}
              sub={`${data.hit} correct of ${data.total}`}
            />
            <OverallStat
              label="Signals"
              value={data.total.toLocaleString()}
              sub={`last ${data.period_days} days`}
            />
            {data.by_confidence?.map(c => (
              <OverallStat
                key={c.confidence}
                label={`${c.confidence} conf.`}
                value={c.hit_rate != null ? `${c.hit_rate}%` : '—'}
                sub={`${c.total} signals`}
              />
            ))}
          </div>

          {/* By Market table */}
          {data.by_market?.length > 0 && (
            <div>
              <h4 className="text-[11px] font-semibold text-[var(--text-h)] uppercase tracking-wide opacity-60 mb-2">
                Hit Rate by Market
              </h4>
              <div className="space-y-2">
                {data.by_market.map(row => (
                  <div key={row.market} className="rounded-lg border border-[var(--border)] bg-[var(--code-bg)] px-4 py-2.5">
                    <div className="flex items-center justify-between gap-3 mb-1.5">
                      <span className="text-xs font-medium text-[var(--text-h)] truncate">{row.market}</span>
                      <div className="flex items-center gap-3 shrink-0 text-xs tabular-nums">
                        <span className="text-[var(--text)] opacity-50">{row.total} signals</span>
                        <span className={`font-bold ${hitColor(row.hit_rate)}`}>
                          {row.hit_rate != null ? `${row.hit_rate}%` : '—'}
                        </span>
                      </div>
                    </div>
                    <HitBar rate={row.hit_rate || 0} maxRate={Math.max(...data.by_market.map(r => r.hit_rate || 0), 80)} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* By League Tier */}
          {data.by_league_tier?.length > 0 && (
            <div>
              <h4 className="text-[11px] font-semibold text-[var(--text-h)] uppercase tracking-wide opacity-60 mb-2">
                Hit Rate by League Tier
              </h4>
              <div className="flex flex-wrap gap-3">
                {data.by_league_tier.map(row => {
                  const tierLabel = row.tier === 1 ? 'Tier 1 (Top Leagues)' : row.tier === 2 ? 'Tier 2 (Second Div.)' : 'Tier 3 (Lower Div.)'
                  return (
                    <div key={row.tier} className="flex flex-col gap-1 px-4 py-3 rounded-lg border border-[var(--border)] bg-[var(--code-bg)] min-w-[130px]">
                      <span className={`text-lg font-bold tabular-nums ${hitColor(row.hit_rate)}`}>
                        {row.hit_rate != null ? `${row.hit_rate}%` : '—'}
                      </span>
                      <span className="text-[10px] text-[var(--text)] opacity-60">{tierLabel}</span>
                      <span className="text-[9px] text-[var(--text)] opacity-40">{row.total} signals</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {data.total === 0 && (
            <p className="text-xs text-[var(--text)] opacity-50 text-center py-4">
              No settled signals in this period yet — data appears after matches finish.
            </p>
          )}
        </>
      )}
    </div>
  )
}
