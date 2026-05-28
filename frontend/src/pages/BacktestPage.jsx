import { useState } from 'react'
import { FlaskConical, BarChart2, TableProperties } from 'lucide-react'
import { runBacktest } from '../api/backtest'
import BacktestControls from '../components/backtest/BacktestControls'
import BankrollChart from '../components/backtest/BankrollChart'
import MarketStatsTable from '../components/backtest/MarketStatsTable'
import UpgradePrompt from '../components/shared/UpgradePrompt'
import useTier from '../hooks/useTier'

function Section({ icon: Icon, title, subtitle, children }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-3 border-b border-[var(--border)] bg-[var(--code-bg)]">
        {Icon && <Icon size={14} className="text-[var(--accent)] shrink-0" />}
        <span className="text-sm font-semibold text-[var(--text-h)]">{title}</span>
        {subtitle && (
          <span className="text-xs text-[var(--text)] opacity-55 hidden sm:inline">{subtitle}</span>
        )}
      </div>
      <div className="p-5">{children}</div>
    </div>
  )
}

function RunContext({ params }) {
  if (!params?._labels) return null
  const { market, engine, confidence, min_edge } = params._labels
  const dateLabel = params.date_from && params.date_to
    ? `${params.date_from} → ${params.date_to}`
    : null

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
      <span className="text-[var(--text)] opacity-45 uppercase tracking-wide font-semibold">Tested:</span>
      {[market, engine, confidence, min_edge, dateLabel].filter(Boolean).map((label, i) => (
        <span
          key={i}
          className="px-2 py-0.5 rounded-full border border-[var(--border)] bg-[var(--code-bg)] text-[var(--text)] opacity-70"
        >
          {label}
        </span>
      ))}
    </div>
  )
}

export default function BacktestPage({ onUpgrade }) {
  const { isPro } = useTier()
  const [loading,      setLoading]      = useState(false)
  const [summary,      setSummary]      = useState(null)
  const [curve,        setCurve]        = useState([])
  const [error,        setError]        = useState(null)
  const [lastParams,   setLastParams]   = useState(null)

  async function handleRun(params) {
    setLoading(true)
    setError(null)
    setSummary(null)
    setCurve([])
    setLastParams(params)
    try {
      const result = await runBacktest(params)
      setSummary(result)
      setCurve(result.bankroll_curve ?? [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  if (!isPro) {
    return (
      <UpgradePrompt
        required="pro"
        feature="Backtesting replays the engine's signals over historical data to measure edge, ROI, strike rate, and bankroll growth across all markets and leagues."
        onUpgrade={onUpgrade}
      />
    )
  }

  const hasResults = summary && summary.total > 0
  const noResults  = summary && summary.total === 0

  return (
    <div className="space-y-5">

      {/* Controls */}
      <Section icon={FlaskConical} title="Backtest Configuration">
        <BacktestControls onRun={handleRun} loading={loading} />
      </Section>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Empty state — no run yet */}
      {!loading && !summary && !error && (
        <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-12 flex flex-col items-center gap-3 text-center">
          <FlaskConical size={32} className="text-[var(--text)] opacity-25" />
          <p className="text-sm font-semibold text-[var(--text-h)]">Ready to backtest</p>
          <p className="text-xs text-[var(--text)] opacity-80 max-w-sm">
            Choose your parameters above and click{' '}
            <span className="font-semibold text-[var(--accent)]">Run Backtest</span>{' '}
            to simulate the engine against historical data.
          </p>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-12 flex flex-col items-center gap-3 text-center">
          <div className="w-8 h-8 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-[var(--text)] opacity-65">Running backtest…</p>
          <p className="text-xs text-[var(--text)] opacity-65">
            Replaying signals across all fixtures in the date range
          </p>
        </div>
      )}

      {/* Zero results */}
      {!loading && noResults && (
        <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-8 flex flex-col items-center gap-2 text-center">
          <p className="text-sm font-semibold text-[var(--text-h)]">No qualifying signals in this range</p>
          <p className="text-xs text-[var(--text)] opacity-70 max-w-md">
            The selected date range, market, and confidence filters produced no bets that passed
            the engine thresholds. Try a wider date range, lower the minimum edge, or include
            more confidence tiers.
          </p>
          <RunContext params={lastParams} />
        </div>
      )}

      {/* Results */}
      {!loading && hasResults && (
        <>
          {/* Run context badge */}
          <RunContext params={lastParams} />

          {/* Bankroll curve */}
          <Section icon={BarChart2} title="Bankroll Curve" subtitle="cumulative performance over all simulated bets">
            <BankrollChart curve={curve} />
          </Section>

          {/* Market breakdown */}
          <Section icon={TableProperties} title="Results by Market" subtitle="sorted by ROI by default — click any column to resort">
            <MarketStatsTable summary={summary} />
          </Section>
        </>
      )}

    </div>
  )
}
