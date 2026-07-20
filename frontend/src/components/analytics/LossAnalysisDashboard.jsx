import { useState, useEffect } from 'react'
import { Brain, AlertTriangle, TrendingDown, RefreshCw, ChevronDown, ChevronUp, Zap, Shield } from 'lucide-react'
import { fetchLossAnalysisSummary, triggerLossAnalysisPipeline } from '../../api/analytics'
import LoadingSpinner from '../shared/LoadingSpinner'

// ── Failure category config ───────────────────────────────────────────────────

const CATEGORY_META = {
  high_odds_risk:        { label: 'High Odds Risk',         color: 'bg-red-500/15 text-red-500 border-red-500/30' },
  market_mispricing:     { label: 'Market Mispricing',      color: 'bg-orange-500/15 text-orange-500 border-orange-500/30' },
  tier3_exposure:        { label: 'Tier 3 Exposure',        color: 'bg-amber-500/15 text-amber-600 border-amber-500/30' },
  zero_zero:             { label: '0-0 Game',               color: 'bg-slate-500/15 text-slate-400 border-slate-500/30' },
  away_team_blank:       { label: 'Away Blank',             color: 'bg-blue-500/15 text-blue-400 border-blue-500/30' },
  home_team_blank:       { label: 'Home Blank',             color: 'bg-indigo-500/15 text-indigo-400 border-indigo-500/30' },
  end_of_season:         { label: 'End of Season',          color: 'bg-purple-500/15 text-purple-400 border-purple-500/30' },
  defensive_game:        { label: 'Defensive Game',         color: 'bg-teal-500/15 text-teal-400 border-teal-500/30' },
  model_overconfidence:  { label: 'Model Overconfident',    color: 'bg-rose-500/15 text-rose-500 border-rose-500/30' },
  data_gap:              { label: 'Data Gap',               color: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30' },
  genuine_variance:      { label: 'Genuine Variance',       color: 'bg-green-500/15 text-green-500 border-green-500/30' },
}

function CategoryBadge({ category }) {
  const meta = CATEGORY_META[category] || { label: category, color: 'bg-[var(--code-bg)] text-[var(--text)] border-[var(--border)]' }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border tracking-wide ${meta.color}`}>
      {meta.label}
    </span>
  )
}

// ── Avoidability gauge ────────────────────────────────────────────────────────

function AvoidabilityGauge({ score }) {
  if (score == null) return null
  const pct = (score / 10) * 100
  const color = score >= 7 ? 'bg-red-500' : score >= 4 ? 'bg-amber-500' : 'bg-green-500'
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-[var(--code-bg)] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-[var(--text)] opacity-75">{score}/10</span>
    </div>
  )
}

// ── Individual loss card ──────────────────────────────────────────────────────

function LossCard({ analysis }) {
  const [expanded, setExpanded] = useState(false)
  const cats = (analysis.categories || []).filter(Boolean)

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--card-bg,var(--code-bg))] overflow-hidden">
      <div className="px-4 py-3 space-y-2">

        {/* Header row */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] text-[var(--text)] opacity-70">{analysis.date}</span>
          {analysis.tier && (
            <span className={`w-2 h-2 rounded-full shrink-0 inline-block ${
              analysis.tier === 1 ? 'bg-amber-400' : analysis.tier === 2 ? 'bg-slate-400' : 'bg-slate-600'
            }`} title={`Tier ${analysis.tier}`} />
          )}
          {analysis.league && (
            <span className="text-[10px] text-[var(--text)] opacity-80 truncate">{analysis.league}</span>
          )}
          {analysis.score && (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-slate-500/15 text-slate-400 border border-slate-500/25">
              {analysis.score}
            </span>
          )}
        </div>

        {/* Match + market */}
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-[var(--text-h)] leading-tight">{analysis.match}</p>
            <p className="text-xs text-[var(--text)] opacity-70 mt-0.5">
              {analysis.market} · <span className="font-mono">{analysis.odds?.toFixed(2)}</span>
            </p>
          </div>
          <AvoidabilityGauge score={analysis.avoidability} />
        </div>

        {/* Failure categories */}
        {cats.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {cats.map(cat => <CategoryBadge key={cat} category={cat} />)}
          </div>
        )}
      </div>

      {/* Expandable narrative */}
      <div className="border-t border-[var(--border)] px-4 py-2">
        <button
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-1 text-xs text-[var(--text)] hover:text-[var(--accent)] transition-colors"
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          AI Analysis
        </button>
      </div>

      {expanded && (
        <div className="px-4 pb-4 space-y-2 border-t border-[var(--border)] bg-[var(--code-bg)]">
          {analysis.narrative && (
            <div className="pt-3">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text)] opacity-70 mb-1">Why It Lost</p>
              <p className="text-xs text-[var(--text)] opacity-90 leading-relaxed">{analysis.narrative}</p>
            </div>
          )}
          {analysis.recommendation && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text)] opacity-70 mb-1">System Recommendation</p>
              <p className="text-xs text-amber-500 leading-relaxed">{analysis.recommendation}</p>
            </div>
          )}
          <p className="text-[9px] text-[var(--text)] opacity-30">Agent: {analysis.agent}</p>
        </div>
      )}
    </div>
  )
}

// ── Category frequency chart ──────────────────────────────────────────────────

function CategoryBreakdown({ categoryCounts, totalLosses }) {
  if (!categoryCounts || !Object.keys(categoryCounts).length) return null
  const sorted = Object.entries(categoryCounts).sort((a, b) => b[1] - a[1]).slice(0, 8)
  const max = sorted[0]?.[1] || 1

  return (
    <div className="space-y-2">
      {sorted.map(([cat, count]) => {
        const meta = CATEGORY_META[cat] || { label: cat, color: 'bg-[var(--accent)]' }
        const pct = Math.round((count / totalLosses) * 100)
        return (
          <div key={cat} className="flex items-center gap-3">
            <span className="text-[10px] text-[var(--text)] opacity-75 w-36 shrink-0 truncate">{meta.label}</span>
            <div className="flex-1 h-2 bg-[var(--code-bg)] rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${meta.color.split(' ')[0]}`}
                style={{ width: `${(count / max) * 100}%` }}
              />
            </div>
            <span className="text-[10px] font-mono text-[var(--text)] opacity-80 w-12 text-right shrink-0">
              {count}× ({pct}%)
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ── Main dashboard ────────────────────────────────────────────────────────────

export default function LossAnalysisDashboard() {
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState(null)
  const [error, setError] = useState(null)
  const [lookback, setLookback] = useState(30)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchLossAnalysisSummary(lookback)
      setSummary(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [lookback])

  const runPipeline = async () => {
    setRunning(true)
    setRunResult(null)
    try {
      const result = await triggerLossAnalysisPipeline(90)
      setRunResult(result)
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="space-y-5">

      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <Brain size={16} className="text-[var(--accent)]" />
            <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-[var(--text)] opacity-75">
              AI Loss Analysis
            </h2>
          </div>
          <p className="mt-1 text-sm text-[var(--text)] opacity-70">
            Self-learning engine — four AI agents analyse every loss, detect patterns, and propose threshold improvements.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={lookback}
            onChange={e => setLookback(Number(e.target.value))}
            className="text-xs rounded-lg border border-[var(--border)] bg-[var(--code-bg)] text-[var(--text)] px-3 py-1.5 outline-none"
          >
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={60}>Last 60 days</option>
            <option value={90}>Last 90 days</option>
          </select>
          <button
            onClick={runPipeline}
            disabled={running}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-[var(--accent)] text-white font-semibold hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {running ? <LoadingSpinner size="xs" /> : <Zap size={11} />}
            {running ? 'Analysing…' : 'Run AI Pipeline'}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Pipeline run result */}
      {runResult && (
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/8 px-4 py-3 space-y-1">
          <p className="text-xs font-semibold text-emerald-400">
            Pipeline complete — {runResult.bets_analysed} bet{runResult.bets_analysed !== 1 ? 's' : ''} analysed
          </p>
          {runResult.accepted_proposals?.length > 0 && (
            <div className="space-y-1 mt-2">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-emerald-400 opacity-70">
                Accepted Threshold Proposals
              </p>
              {runResult.accepted_proposals.map((p, i) => (
                <div key={i} className="text-xs text-[var(--text)] opacity-80 flex items-start gap-2">
                  <Shield size={10} className="text-emerald-400 mt-0.5 shrink-0" />
                  <span><strong>{p.target}</strong>: {p.rationale} (backtest: {p.backtest})</span>
                </div>
              ))}
            </div>
          )}
          {runResult.skipped_proposals?.length > 0 && (
            <p className="text-[10px] text-amber-500 opacity-75">
              {runResult.skipped_proposals.length} proposal{runResult.skipped_proposals.length !== 1 ? 's' : ''} rejected by backtester
            </p>
          )}
        </div>
      )}

      {loading && (
        <div className="flex justify-center py-10">
          <LoadingSpinner size="md" />
        </div>
      )}

      {!loading && summary && (
        <>
          {/* KPI row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              {
                label: 'Losses Analysed',
                value: summary.total_losses_analysed,
                icon: <TrendingDown size={14} className="text-red-400" />,
              },
              {
                label: 'Avg Avoidability',
                value: summary.avg_avoidability != null ? `${summary.avg_avoidability}/10` : '—',
                icon: <AlertTriangle size={14} className="text-amber-400" />,
              },
              {
                label: 'Top Failure',
                value: summary.category_counts
                  ? (CATEGORY_META[Object.keys(summary.category_counts)[0]]?.label || Object.keys(summary.category_counts)[0])
                  : '—',
                icon: <Brain size={14} className="text-[var(--accent)]" />,
                small: true,
              },
              {
                label: 'Most Avoidable Market',
                value: summary.most_avoidable_market || '—',
                icon: <Shield size={14} className="text-rose-400" />,
                small: true,
              },
            ].map(({ label, value, icon, small }) => (
              <div key={label} className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3">
                <div className="flex items-center gap-1.5 mb-1">
                  {icon}
                  <span className="text-[10px] uppercase tracking-widest text-[var(--text)] opacity-70 font-semibold">{label}</span>
                </div>
                <p className={`font-bold text-[var(--text-h)] ${small ? 'text-sm' : 'text-xl'}`}>{value}</p>
              </div>
            ))}
          </div>

          {/* Category breakdown */}
          {summary.category_counts && Object.keys(summary.category_counts).length > 0 && (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 py-4">
              <h3 className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text)] opacity-70 mb-3">
                Failure Category Frequency
              </h3>
              <CategoryBreakdown
                categoryCounts={summary.category_counts}
                totalLosses={summary.total_losses_analysed}
              />
            </div>
          )}

          {/* Individual loss cards */}
          {summary.analyses?.length > 0 ? (
            <div className="space-y-3">
              <h3 className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text)] opacity-70">
                Loss Details ({summary.analyses.length})
              </h3>
              <div className="grid gap-3 lg:grid-cols-2">
                {summary.analyses.map(a => (
                  <LossCard key={a.id} analysis={a} />
                ))}
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-6 py-10 text-center">
              <Brain size={28} className="mx-auto mb-3 text-[var(--text)] opacity-30" />
              <p className="text-sm text-[var(--text)] opacity-80">No loss analyses yet.</p>
              <p className="text-xs text-[var(--text)] opacity-65 mt-1">
                Click "Run AI Pipeline" to analyse settled losses.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
