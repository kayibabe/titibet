import { useState, useEffect, useCallback, useMemo } from 'react'
import { Brain, Activity, BarChart2, Layers, TrendingUp, Zap, TrendingDown, RefreshCw, ChevronUp, ChevronDown, ChevronsUpDown, Target, Coins, Crosshair, Users } from 'lucide-react'
import { fetchAnalytics, fetchAnalyticsIntelligence, fetchStakingSimulation, fetchProbabilityCalibration, fetchAccaPerformance } from '../api/analytics'
import AccuracyDashboard from '../components/analytics/AccuracyDashboard'
import Leaderboard from '../components/analytics/Leaderboard'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, BarChart, Bar, Cell } from 'recharts'
import LossAnalysisDashboard from '../components/analytics/LossAnalysisDashboard'
import BriefingPanel from '../components/analytics/BriefingPanel'
import ModelIntelligenceDashboard from '../components/analytics/ModelIntelligenceDashboard'
import ParameterHub from '../components/analytics/ParameterHub'
import KPIRow from '../components/analytics/KPIRow'
import TrendChart from '../components/analytics/TrendChart'
import ByMarketTable from '../components/analytics/ByMarketTable'
import ConfidenceBreakdown from '../components/analytics/ConfidenceBreakdown'
import StreakBadge from '../components/analytics/StreakBadge'
import LeagueInsights from '../components/analytics/LeagueInsights'
import MarketHeatmap from '../components/analytics/MarketHeatmap'
import LoadingSpinner from '../components/shared/LoadingSpinner'
import DatePicker from '../components/shared/DatePicker'
import UpgradePrompt from '../components/shared/UpgradePrompt'
import SampleSizeWarning from '../components/shared/SampleSizeWarning'
import useTier from '../hooks/useTier'

// ── Sortable table helpers ────────────────────────────────────────────────────

function useSortable(defaultCol, defaultDir = 'desc') {
  const [col, setCol] = useState(defaultCol)
  const [dir, setDir] = useState(defaultDir)
  function toggle(c) {
    if (col === c) setDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setCol(c); setDir('desc') }
  }
  function sorted(rows, numeric = true) {
    return [...rows].sort((a, b) => {
      const va = a[col] ?? (numeric ? -Infinity : '')
      const vb = b[col] ?? (numeric ? -Infinity : '')
      if (typeof va === 'string') return dir === 'desc' ? vb.localeCompare(va) : va.localeCompare(vb)
      return dir === 'desc' ? vb - va : va - vb
    })
  }
  return { col, dir, toggle, sorted }
}

function SortTh({ label, col, sort, align = 'right', className = '' }) {
  const active = sort.col === col
  const Icon = active ? (sort.dir === 'desc' ? ChevronDown : ChevronUp) : ChevronsUpDown
  return (
    <th
      onClick={() => sort.toggle(col)}
      className={`px-3 py-2 font-medium cursor-pointer select-none whitespace-nowrap transition-colors
        hover:text-[var(--text-h)] ${active ? 'text-[var(--accent)]' : 'text-[var(--text)] opacity-70'}
        ${align === 'left' ? 'text-left' : 'text-right'} ${className}`}
    >
      <span className={`inline-flex items-center gap-1 ${align === 'right' ? 'justify-end' : ''}`}>
        {label}
        <Icon size={11} className={active ? 'text-[var(--accent)]' : 'opacity-30'} />
      </span>
    </th>
  )
}

// ─────────────────────────────────────────────────────────────────────────────

function todayStr() { return new Date().toISOString().slice(0, 10) }
function daysAgo(n) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}
function formatPeriodLabel(from, to) {
  const fmt = d => new Date(d + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
  return fmt(from) + ' - ' + fmt(to)
}

const DATE_PRESETS = [
  { label: '7d',  from: () => daysAgo(7) },
  { label: '14d', from: () => daysAgo(14) },
  { label: '30d', from: () => daysAgo(30) },
  { label: '90d', from: () => daysAgo(90) },
  { label: 'All', from: () => '' },
]

function Section({ icon: Icon, title, subtitle, children, pro = false, locked = false, onUpgrade }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-[var(--border)] bg-[var(--code-bg)]">
        <div className="flex items-center gap-2">
          {Icon && <Icon size={14} className="text-[var(--accent)] shrink-0" />}
          <span className="text-sm font-semibold text-[var(--text-h)]">{title}</span>
          {subtitle && <span className="text-xs text-[var(--text)] opacity-80 hidden sm:inline">{subtitle}</span>}
        </div>
        {pro && (
          <span className="text-[9px] font-bold text-[var(--accent)] bg-[var(--accent-bg,rgba(99,102,241,0.12))] border border-[var(--accent-border,rgba(99,102,241,0.25))] px-1.5 py-0.5 rounded tracking-wide">
            PRO
          </span>
        )}
      </div>
      <div className="p-5">
        {locked ? (
          <UpgradePrompt
            required="pro"
            variant="inline"
            feature={title + ' requires a Pro subscription.'}
            onUpgrade={onUpgrade}
          />
        ) : children}
      </div>
    </div>
  )
}

function FactorBadge({ factor }) {
  if (factor == null) return null
  const isBoost   = factor > 1.05
  const isPenalty = factor < 0.95
  if (!isBoost && !isPenalty) return <span className="text-[10px] text-[var(--text)] opacity-65 tabular-nums">1.00x</span>
  const cls = isBoost
    ? 'bg-green-500/15 text-green-400 border-green-500/30'
    : 'bg-red-500/15 text-red-400 border-red-500/30'
  return (
    <span className={'inline-flex items-center gap-0.5 text-[10px] font-bold px-1.5 py-0.5 rounded border ' + cls}>
      {isBoost ? 'Up' : 'Down'} {factor.toFixed(2)}x
    </span>
  )
}

// ── Sortable insight tables ───────────────────────────────────────────────────

function MarketWeightTable({ rows }) {
  const sort = useSortable('roi')
  const data = useMemo(() => sort.sorted(rows), [rows, sort.col, sort.dir]) // eslint-disable-line
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[580px] text-xs">
        <thead>
          <tr className="border-b border-[var(--border)]">
            <SortTh label="Market"   col="market"             sort={sort} align="left" />
            <SortTh label="Bets"     col="samples"            sort={sort} />
            <SortTh label="Hit Rate" col="win_rate"           sort={sort} />
            <SortTh label="ROI"      col="roi"                sort={sort} />
            <SortTh label="Weight"   col="performance_factor" sort={sort} />
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => {
            const roiColor = row.roi >= 10 ? 'text-green-400' : row.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
            const hrColor  = row.win_rate >= 60 ? 'text-green-400' : 'text-[var(--text-h)]'
            return (
              <tr key={i} className="border-t border-[var(--border)] hover:bg-[var(--code-bg)]">
                <td className="px-3 py-2 font-medium text-[var(--text-h)]">{row.market}</td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--text)]">{row.samples}</td>
                <td className={'px-3 py-2 text-right tabular-nums font-semibold ' + hrColor}>{row.win_rate?.toFixed(1)}%</td>
                <td className={'px-3 py-2 text-right tabular-nums font-semibold ' + roiColor}>{row.roi >= 0 ? '+' : ''}{row.roi?.toFixed(1)}%</td>
                <td className="px-3 py-2 text-right"><FactorBadge factor={row.performance_factor} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function RuleWeightTable({ rows }) {
  const sort = useSortable('roi')
  const data = useMemo(() => sort.sorted(rows), [rows, sort.col, sort.dir]) // eslint-disable-line
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[580px] text-xs">
        <thead>
          <tr className="border-b border-[var(--border)]">
            <SortTh label="Rule"     col="rule_key"           sort={sort} align="left" />
            <SortTh label="Bets"     col="samples"            sort={sort} />
            <SortTh label="Hit Rate" col="win_rate"           sort={sort} />
            <SortTh label="ROI"      col="roi"                sort={sort} />
            <SortTh label="Weight"   col="performance_factor" sort={sort} />
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => {
            const roiColor = row.roi >= 10 ? 'text-green-400' : row.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
            const hrColor  = row.win_rate >= 60 ? 'text-green-400' : 'text-[var(--text-h)]'
            return (
              <tr key={i} className="border-t border-[var(--border)] hover:bg-[var(--code-bg)]">
                <td className="px-3 py-2 font-mono text-[var(--text-h)]">{row.rule_key}</td>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--text)]">{row.samples}</td>
                <td className={'px-3 py-2 text-right tabular-nums font-semibold ' + hrColor}>{row.win_rate?.toFixed(1)}%</td>
                <td className={'px-3 py-2 text-right tabular-nums font-semibold ' + roiColor}>{row.roi >= 0 ? '+' : ''}{row.roi?.toFixed(1)}%</td>
                <td className="px-3 py-2 text-right"><FactorBadge factor={row.performance_factor} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function MarketTierTable({ rows }) {
  const sort = useSortable('roi')
  const data = useMemo(() => sort.sorted(rows), [rows, sort.col, sort.dir]) // eslint-disable-line
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-xs">
        <thead>
          <tr className="border-b border-[var(--border)]">
            <SortTh label="Market"   col="market"             sort={sort} align="left" />
            <SortTh label="Tier"     col="league_tier"        sort={sort} align="left" />
            <SortTh label="Bets"     col="samples"            sort={sort} />
            <SortTh label="Hit Rate" col="win_rate"           sort={sort} />
            <SortTh label="ROI"      col="roi"                sort={sort} />
            <SortTh label="Weight"   col="performance_factor" sort={sort} />
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr key={i} className="border-t border-[var(--border)] hover:bg-[var(--code-bg)]">
              <td className="px-3 py-2 font-medium text-[var(--text-h)]">{row.market}</td>
              <td className="px-3 py-2 text-[var(--text)]">{row.tier_label}</td>
              <td className="px-3 py-2 text-right tabular-nums text-[var(--text)]">{row.samples}</td>
              <td className="px-3 py-2 text-right tabular-nums font-semibold text-[var(--text-h)]">{row.win_rate?.toFixed(1)}%</td>
              <td className={'px-3 py-2 text-right tabular-nums font-semibold ' + (row.roi >= 0 ? 'text-green-400' : 'text-red-400')}>
                {row.roi >= 0 ? '+' : ''}{row.roi?.toFixed(1)}%
              </td>
              <td className="px-3 py-2 text-right"><FactorBadge factor={row.performance_factor} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────

function MarketPerformanceContent({ rows, onApplySignalFilter }) {
  const [view, setView] = useState('table')   // 'table' | 'heatmap'
  return (
    <div className="space-y-4">
      <MarketInsights rows={rows} onApplySignalFilter={onApplySignalFilter} />

      {/* View toggle */}
      <div className="flex items-center gap-1 border-b border-[var(--border)] pb-0">
        {[{ id: 'table', label: 'Table' }, { id: 'heatmap', label: 'Heatmap' }].map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setView(id)}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors ${
              view === id
                ? 'border-[var(--accent)] text-[var(--accent)]'
                : 'border-transparent text-[var(--text)] opacity-80 hover:opacity-100 hover:text-[var(--text-h)]'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {view === 'table'   && <ByMarketTable rows={rows} title="" keyField="market" onFilterSignals={onApplySignalFilter} />}
      {view === 'heatmap' && <MarketHeatmap rows={rows} />}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────

function WeightsSectionHeader({ label, count }) {
  return (
    <div className="flex items-center gap-2">
      <h4 className="text-[11px] font-semibold text-[var(--text-h)] uppercase tracking-wide opacity-70">{label}</h4>
      {count != null && count > 0 && (
        <span className="inline-flex items-center justify-center h-4 w-4 rounded-full bg-red-500/20 text-red-400 text-[9px] font-bold">{count}</span>
      )}
    </div>
  )
}

function ModelInsightsContent({ insights }) {
  if (!insights) return null

  const {
    by_confidence = [],
    by_market = [],
    by_rule = [],
    calibration = [],
    by_market_tier = [],
    auto_suppress_rules = [],
    auto_suppress_market_tiers = [],
    auto_suppress_league_markets = [],
  } = insights

  const hasData = by_confidence.length || by_market.length || by_rule.length || calibration.length
  if (!hasData) {
    return (
      <p className="text-xs text-[var(--text)] opacity-70 text-center py-4">
        Need at least 5 settled bets per signal tier before weights activate.
        Keep tracking and settling — the engine will self-calibrate soon.
      </p>
    )
  }

  const suppressCount = auto_suppress_rules.length + auto_suppress_market_tiers.length + auto_suppress_league_markets.length

  return (
    <div className="space-y-6">
      <p className="text-xs text-[var(--text)] opacity-70">
        Computed from full settled history (not the date filter above).
        <span className="text-green-400 ml-1">Green = boosted</span>
        {' · '}
        <span className="text-red-400">Red = penalised</span>
        {' · '}
        <span className="opacity-80">1.00x = neutral</span>
      </p>

      {/* Confidence weights */}
      {by_confidence.length > 0 && (
        <div className="space-y-2">
          <WeightsSectionHeader label="Confidence" />
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {by_confidence.map((row, i) => {
              const roiColor = row.roi >= 10 ? 'text-green-400' : row.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
              const hrColor  = row.win_rate >= 60 ? 'text-green-400' : row.win_rate >= 50 ? 'text-yellow-400' : 'text-red-400'
              return (
                <div key={i} className="rounded-lg border border-[var(--border)] bg-[var(--code-bg)] px-3 py-2.5 space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-[var(--text-h)]">{row.confidence}</span>
                    <FactorBadge factor={row.performance_factor} />
                  </div>
                  <div className="flex gap-4 text-[11px]">
                    <span className={hrColor}>{row.win_rate?.toFixed(1)}% hit</span>
                    <span className={'font-mono ' + roiColor}>{row.roi >= 0 ? '+' : ''}{row.roi?.toFixed(1)}% ROI</span>
                  </div>
                  <div className="text-[10px] text-[var(--text)] opacity-70">{row.samples} settled</div>
                </div>
              )
            })}
          </div>
          {calibration.length > 0 && (
            <div className="space-y-2 pt-1">
              <p className="text-[10px] font-semibold text-[var(--text)] opacity-70 uppercase tracking-wide">Calibration by confidence tier</p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                {calibration.map((row, i) => {
                  const cls = row.is_overconfident
                    ? 'bg-red-500/10 border-red-500/30'
                    : 'bg-emerald-500/10 border-emerald-500/30'
                  return (
                    <div key={i} className={'rounded-lg border px-3 py-2.5 space-y-1.5 ' + cls}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-[var(--text-h)]">{row.confidence}</span>
                        <span className="text-[10px] font-mono font-semibold text-[var(--text)]">{row.samples} bets</span>
                      </div>
                      <div className="text-[11px] text-[var(--text-h)]">
                        Expected {row.expected_win_rate?.toFixed(1)}% · Actual {row.actual_win_rate?.toFixed(1)}%
                      </div>
                      <div className="text-[10px] text-[var(--text)] opacity-80">{row.status}</div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Market weights */}
      {by_market.length > 0 && (
        <div className="space-y-2">
          <WeightsSectionHeader label="Market weights" />
          <MarketWeightTable rows={by_market} />
        </div>
      )}

      {/* Rule weights */}
      {by_rule.length > 0 && (
        <div className="space-y-2">
          <WeightsSectionHeader label="Rule weights" />
          <RuleWeightTable rows={by_rule} />
        </div>
      )}

      {/* Market × Tier */}
      {by_market_tier.length > 0 && (
        <div className="space-y-2">
          <WeightsSectionHeader label="Market × Tier" />
          <MarketTierTable rows={by_market_tier} />
        </div>
      )}

      {/* Auto-suppression */}
      <div className="space-y-2">
        <WeightsSectionHeader label="Auto-suppression" count={suppressCount} />
        {suppressCount > 0 ? (
          <div className="space-y-3">
            {auto_suppress_rules.length > 0 && (
              <div>
                <p className="text-[10px] font-semibold text-[var(--text)] opacity-80 uppercase tracking-wide mb-1.5">Rules</p>
                <div className="flex flex-wrap gap-2">
                  {auto_suppress_rules.map(rule => (
                    <span key={rule} className="inline-flex rounded-full border border-red-500/25 bg-red-500/10 px-2 py-1 text-[10px] font-mono text-red-300">{rule}</span>
                  ))}
                </div>
              </div>
            )}
            {auto_suppress_market_tiers.length > 0 && (
              <div>
                <p className="text-[10px] font-semibold text-[var(--text)] opacity-80 uppercase tracking-wide mb-1.5">Market × League Tier</p>
                <div className="flex flex-wrap gap-2">
                  {auto_suppress_market_tiers.map((item, i) => (
                    <span key={i} className="inline-flex rounded-full border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-300">
                      {item.market} · Tier {item.league_tier}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {auto_suppress_league_markets.length > 0 && (
              <div>
                <p className="text-[10px] font-semibold text-[var(--text)] opacity-80 uppercase tracking-wide mb-1.5">League × Market</p>
                <div className="flex flex-wrap gap-2">
                  {auto_suppress_league_markets.slice(0, 10).map((item, i) => (
                    <span key={i} className="inline-flex rounded-full border border-fuchsia-500/25 bg-fuchsia-500/10 px-2 py-1 text-[10px] text-fuchsia-300">
                      {item.league} · {item.market}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-[var(--text)] opacity-80">
            No suppressions active — the engine treats all markets and leagues equally.
          </p>
        )}
      </div>
    </div>
  )
}

function MarketInsights({ rows, onApplySignalFilter }) {
  if (!rows || rows.length === 0) return null
  const settled = rows.filter(r => (r.wins ?? 0) + (r.losses ?? 0) >= 3)
  if (settled.length < 2) return null

  const byRoi = [...settled].sort((a, b) => b.roi - a.roi)
  const best  = byRoi[0]
  const worst = byRoi[byRoi.length - 1]
  const topHit = [...settled].sort((a, b) => b.win_rate - a.win_rate)[0]

  const chips = [
    { label: 'Best ROI',      market: best.market,   stat: `${best.roi >= 0 ? '+' : ''}${best.roi.toFixed(1)}% ROI`,     sub: `${best.wins}W · ${best.losses}L`,         color: 'green' },
    { label: 'Best Hit Rate', market: topHit.market, stat: `${topHit.win_rate.toFixed(1)}% hit rate`,                     sub: `${topHit.bets} bets tracked`,              color: 'blue'  },
    worst.roi < 0
      ? { label: 'Weakest Market', market: worst.market, stat: `${worst.roi.toFixed(1)}% ROI`, sub: `${worst.wins}W · ${worst.losses}L · consider avoiding`, color: 'red' }
      : null,
  ].filter(Boolean)

  const c = {
    green: { border: 'border-green-500/20', bg: 'bg-green-500/5',  text: 'text-green-400' },
    blue:  { border: 'border-blue-500/20',  bg: 'bg-blue-500/5',   text: 'text-blue-400'  },
    red:   { border: 'border-red-500/20',   bg: 'bg-red-500/5',    text: 'text-red-400'   },
  }

  return (
    <div className={`grid grid-cols-1 ${chips.length === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-2'} gap-2`}>
      {chips.map((chip, i) => (
        <div key={i} className={`rounded-lg border ${c[chip.color].border} ${c[chip.color].bg} px-3 py-2.5 flex flex-col`}>
          <p className={`text-[10px] font-semibold uppercase tracking-wide mb-0.5 ${c[chip.color].text}`}>{chip.label}</p>
          <p className={`text-sm font-bold ${c[chip.color].text} truncate leading-tight`} title={chip.market}>{chip.market}</p>
          <p className="text-[11px] text-[var(--text)] opacity-70 mt-0.5">{chip.stat}</p>
          <p className="text-[10px] text-[var(--text)] opacity-70 leading-snug">{chip.sub}</p>
          {onApplySignalFilter && (
            <button
              onClick={() => onApplySignalFilter({ market: chip.market, label: `Market: ${chip.market}` })}
              className={`mt-2 self-start text-[10px] font-semibold ${c[chip.color].text} opacity-70 hover:opacity-100 underline underline-offset-2 hover:no-underline transition-opacity`}
            >
              Filter signals →
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

function AgreementBreakdown({ rows, onApplySignalFilter }) {
  if (!rows || rows.length < 2) return null

  const COLORS = {
    'Both':          { bar: 'bg-emerald-500', text: 'text-emerald-400' },
    'Bayesian Only': { bar: 'bg-blue-500',    text: 'text-blue-400' },
    'Poisson Only':  { bar: 'bg-violet-500',  text: 'text-violet-400' },
    'Contradiction': { bar: 'bg-amber-500',   text: 'text-amber-400' },
    'Unknown':       { bar: 'bg-gray-500',    text: 'text-gray-400' },
  }

  const maxBets = Math.max(...rows.map(r => r.bets), 1)

  return (
    <div className="space-y-3">
      {rows.map((row, i) => {
        const c = COLORS[row.agreement] || COLORS['Unknown']
        const roiColor = row.roi >= 10 ? 'text-green-400' : row.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
        const hrColor  = row.win_rate >= 60 ? 'text-green-400' : row.win_rate >= 50 ? 'text-yellow-400' : 'text-red-400'
        const barPct   = Math.round((row.bets / maxBets) * 100)
        return (
          <div key={i} className="rounded-lg border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3">
            <div className="flex items-center justify-between gap-3 mb-2">
              <div className="flex items-center gap-2">
                <span className={'text-xs font-semibold ' + c.text}>{row.agreement}</span>
                {onApplySignalFilter && (
                  <button
                    onClick={() => onApplySignalFilter({ agreement: row.agreement, label: `Agreement: ${row.agreement}` })}
                    className="text-[10px] text-[var(--accent)] opacity-80 hover:opacity-100 underline underline-offset-2 hover:no-underline transition-opacity"
                    title={`Filter signals to ${row.agreement}`}
                  >
                    Filter signals →
                  </button>
                )}
              </div>
              <div className="flex items-center gap-4 text-[11px]">
                <span className="text-[var(--text)] opacity-80">{row.bets} bets</span>
                <span className={'font-mono font-semibold ' + hrColor}>{row.win_rate.toFixed(1)}% hit</span>
                <span className={'font-mono font-semibold ' + roiColor}>{row.roi >= 0 ? '+' : ''}{row.roi.toFixed(1)}% ROI</span>
              </div>
            </div>
            <div className="h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
              <div className={'h-full rounded-full opacity-70 ' + c.bar} style={{ width: barPct + '%' }} />
            </div>
          </div>
        )
      })}
      <p className="text-[10px] text-[var(--text)] opacity-70">
        <span className="text-emerald-400">Both</span> = Bayesian and Poisson agree
        {' · '}
        <span className="text-amber-400">Contradiction</span> = engines disagree
        {' · bars show relative volume'}
      </p>
    </div>
  )
}

function SignalQualityContent({ byConfidence, byAgreement, showFactor, onApplySignalFilter }) {
  const hasBoth = byConfidence.length > 0 && byAgreement.length >= 2
  const [tab, setTab] = useState('Confidence')

  return (
    <div className="space-y-4">
      {hasBoth && (
        <div className="flex gap-1 border-b border-[var(--border)]">
          {['Confidence', 'Agreement'].map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={'px-3 py-1.5 text-xs font-medium rounded-t-md border-b-2 transition-colors ' + (
                tab === t
                  ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--accent-bg,rgba(99,102,241,0.08))]'
                  : 'border-transparent text-[var(--text)] opacity-70 hover:opacity-100 hover:text-[var(--text-h)]'
              )}
            >
              {t}
            </button>
          ))}
        </div>
      )}
      {(!hasBoth || tab === 'Confidence') && byConfidence.length > 0 && (
        <ConfidenceBreakdown rows={byConfidence} title="" showFactor={showFactor} onFilterSignals={onApplySignalFilter} />
      )}
      {(!hasBoth || tab === 'Agreement') && byAgreement.length >= 2 && (
        <AgreementBreakdown rows={byAgreement} onApplySignalFilter={onApplySignalFilter} />
      )}
    </div>
  )
}

function TierBreakdown({ rows = [] }) {
  if (!rows.length) return null

  const TIER_META = {
    1: { label: 'Tier 1', sub: 'Top-flight / international',  ring: 'border-amber-500/30',  bg: 'bg-amber-500/5',  text: 'text-amber-400',  bar: 'bg-amber-400' },
    2: { label: 'Tier 2', sub: 'Second division / major cup', ring: 'border-blue-500/30',   bg: 'bg-blue-500/5',   text: 'text-blue-400',   bar: 'bg-blue-400'  },
    3: { label: 'Tier 3', sub: 'Lower league / minor cup',    ring: 'border-violet-500/30', bg: 'bg-violet-500/5', text: 'text-violet-400', bar: 'bg-violet-400' },
  }

  const totalBets = rows.reduce((s, r) => s + (r.bets ?? 0), 0)

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {rows.map(row => {
          const meta = TIER_META[row.tier] || TIER_META[3]
          const roiColor  = row.roi >= 20 ? 'text-green-400' : row.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
          const hrColor   = row.win_rate >= 60 ? 'text-green-400' : row.win_rate >= 50 ? 'text-yellow-400' : 'text-red-400'
          const sharePct  = totalBets > 0 ? Math.round(row.bets / totalBets * 100) : 0
          return (
            <div key={row.tier} className={`rounded-xl border ${meta.ring} ${meta.bg} p-4 space-y-3`}>
              <div className="flex items-start justify-between">
                <div>
                  <p className={`text-xs font-bold ${meta.text}`}>{meta.label}</p>
                  <p className="text-[10px] text-[var(--text)] opacity-60 mt-0.5">{meta.sub}</p>
                </div>
                <span className="text-[10px] text-[var(--text)] opacity-50 tabular-nums">{sharePct}% of picks</span>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
                <div>
                  <span className="text-[var(--text)] opacity-55">Hit Rate</span>
                  <p className={`font-bold tabular-nums ${hrColor}`}>{row.win_rate.toFixed(1)}%</p>
                </div>
                <div>
                  <span className="text-[var(--text)] opacity-55">ROI</span>
                  <p className={`font-bold tabular-nums ${roiColor}`}>{row.roi >= 0 ? '+' : ''}{row.roi.toFixed(1)}%</p>
                </div>
                <div>
                  <span className="text-[var(--text)] opacity-55">Bets</span>
                  <p className="font-semibold tabular-nums text-[var(--text-h)]">{row.bets}</p>
                </div>
                <div>
                  <span className="text-[var(--text)] opacity-55">Avg odds</span>
                  <p className="font-semibold tabular-nums text-[var(--text-h)]">{row.avg_odds.toFixed(2)}</p>
                </div>
              </div>
              <div>
                <div className="h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
                  <div className={`h-full rounded-full opacity-70 ${meta.bar}`} style={{ width: `${Math.min(100, row.win_rate)}%` }} />
                </div>
                <p className="text-[10px] text-[var(--text)] opacity-45 mt-1 text-right">
                  {row.wins}W · {row.losses}L
                </p>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const ANALYTICS_TABS = [
  { id: 'overview',  label: 'Overview',  desc: 'P&L and performance summary' },
  { id: 'markets',   label: 'Markets',   desc: 'Market, league and signal breakdown' },
  { id: 'strategy',  label: 'Strategy',  desc: 'Staking, calibration and self-learning' },
]

export default function AnalyticsPage({ onUpgrade, onApplySignalFilter, onNavigate, settings }) {
  const { isPro } = useTier()
  const [analyticsTab, setAnalyticsTab] = useState('overview')
  const [dateFrom,     setDateFrom]   = useState('')
  const [dateTo,       setDateTo]     = useState(todayStr)
  const [activePreset, setActivePreset] = useState('All')
  const [refreshKey,   setRefreshKey] = useState(0)
  const [data,         setData]       = useState(null)
  const [insights,     setInsights]   = useState(null)
  const [loading,      setLoading]    = useState(true)
  const [refreshing,   setRefreshing] = useState(false)
  const [error,        setError]      = useState(null)

  function applyPreset(preset) {
    setActivePreset(preset.label)
    setDateFrom(preset.from())
    setDateTo(todayStr())
  }

  const handleRefresh = useCallback(() => setRefreshKey(k => k + 1), [])

  useEffect(() => {
    const isFirstLoad = !data
    if (isFirstLoad) setLoading(true)
    else setRefreshing(true)
    setError(null)

    Promise.all([
      fetchAnalytics({ date_from: dateFrom || undefined, date_to: dateTo || undefined }),
      fetchAnalyticsIntelligence().catch(() => null),
    ])
      .then(([result, intelligence]) => {
        setData({
          summary:      result,
          byMarket:     result.by_market     ?? [],
          byLeague:     result.by_league     ?? [],
          byTier:       result.by_tier       ?? [],
          byRule:       result.by_rule       ?? [],
          byConfidence: result.by_confidence ?? [],
          byAgreement:  result.by_agreement  ?? [],
          bySource:     result.by_source     ?? [],
          trend:        result.daily_trend   ?? [],
          streaks: {
            current_streak_type: result.current_streak_type,
            current_streak_len:  result.current_streak_len,
            longest_win_streak:  result.longest_win_streak,
            longest_loss_streak: result.longest_loss_streak,
          },
        })
        setInsights(intelligence)
      })
      .catch(e => setError(e.message))
      .finally(() => { setLoading(false); setRefreshing(false) })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFrom, dateTo, refreshKey])

  const noData     = !loading && data && (data.summary?.total_bets ?? 0) === 0
  const clvMissing = data && (data.summary?.clv_coverage_pct ?? 0) === 0 && (data.summary?.settled_bets ?? 0) > 0

  return (
    <div className="space-y-5">

      {/* ── Date filter bar ─────────────────────────────────────────────── */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <DatePicker label="From" value={dateFrom} onChange={v => { setDateFrom(v); setActivePreset('') }} />
          <DatePicker label="To"   value={dateTo}   onChange={v => { setDateTo(v);   setActivePreset('') }} />

          <div className="flex items-end gap-1">
            {DATE_PRESETS.map(p => (
              <button
                key={p.label}
                onClick={() => applyPreset(p)}
                className={'px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors ' + (
                  activePreset === p.label
                    ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--accent-bg,rgba(99,102,241,0.1))]'
                    : 'border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--bg)]'
                )}
              >
                {p.label}
              </button>
            ))}
          </div>

          <button
            onClick={handleRefresh}
            disabled={loading || refreshing}
            title="Refresh analytics"
            className={'ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-[var(--border)] transition-colors ' + (
              loading || refreshing
                ? 'opacity-65 cursor-not-allowed text-[var(--text)]'
                : 'text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--bg)]'
            )}
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>

        {dateFrom && dateTo && (
          <p className="text-xs text-[var(--text)] opacity-55">{formatPeriodLabel(dateFrom, dateTo)}</p>
        )}
      </div>

      {loading  && <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>}
      {error    && <p className="text-sm text-red-400 px-1">{error}</p>}

      {/* Sample-size warning — shows until the user has 50+ settled bets */}
      {!loading && data && (
        <SampleSizeWarning
          settledBets={data.summary?.settled_bets ?? 0}
          onNavigate={null}
        />
      )}

      {noData && (
        <div className="rounded-lg border border-blue-500/25 bg-blue-500/8 px-6 py-8 text-center max-w-md mx-auto">
          <h3 className="text-base font-semibold text-blue-400 mb-2">Analytics unlock after you track bets</h3>
          <ol className="list-decimal list-inside text-sm text-left text-slate-300 space-y-1.5 mb-5">
            <li>Go to Signals and click <strong>Track Pick</strong> on a signal</li>
            <li>After the match, mark it Won or Lost in the Tracker</li>
            <li>Once you have 50+ settled bets, full analytics appear</li>
          </ol>
          <button onClick={() => onNavigate?.('signals')} className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded-lg transition-colors">
            View Signals →
          </button>
        </div>
      )}

      {/* ── Intelligence Briefing ── */}
      {!loading && data && !noData && (
        <BriefingPanel data={data} onApplySignalFilter={onApplySignalFilter} />
      )}

      {/* ── Tab bar ── */}
      {!loading && data && !noData && (
        <div className="flex gap-1 border-b border-[var(--border)]">
          {ANALYTICS_TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setAnalyticsTab(t.id)}
              className={`
                flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
                ${analyticsTab === t.id
                  ? 'border-[var(--accent)] text-[var(--accent)]'
                  : 'border-transparent text-[var(--text)] opacity-80 hover:opacity-100 hover:text-[var(--text-h)]'
                }
              `}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {!loading && data && !noData && (
        <>
          {/* ══════════════════════════════════════════════════════════════════
              TAB 1 — OVERVIEW
              Your headline numbers and the P&L curve. Read this first.
          ══════════════════════════════════════════════════════════════════ */}
          {analyticsTab === 'overview' && (
            <div className="space-y-5">
              <Section icon={Target} title="Model Accuracy" subtitle="how often each signal prediction was correct across all settled fixtures">
                <AccuracyDashboard />
              </Section>

              <Section icon={Activity} title="Your Performance" subtitle={activePreset === 'All' ? 'All-time' : activePreset ? `Last ${activePreset}` : dateFrom && dateTo ? formatPeriodLabel(dateFrom, dateTo) : 'period summary'}>
                <div className="space-y-4">
                  <KPIRow summary={data.summary} />
                  <div className="pt-2 border-t border-[var(--border)]">
                    <StreakBadge streaks={data.streaks} />
                  </div>
                  {clvMissing && (
                    <p className="text-[11px] text-[var(--text)] opacity-80 pt-1">
                      <span className="text-amber-400 font-medium">Tip:</span>{' '}
                      Run <span className="font-medium text-[var(--text-h)]">Compute CLV</span> in the Tracker
                      to measure if you're beating closing odds — the strongest long-run edge signal.
                    </p>
                  )}
                </div>
              </Section>

              {/* Calibration lives canonically in Strategy → Model Calibration,
                  so it is not duplicated here on Overview. */}

              {isPro ? (
                <Section icon={BarChart2} title="P&L Trend" subtitle="cumulative profit over time" pro>
                  <TrendChart data={data.trend} />
                </Section>
              ) : (
                <Section icon={BarChart2} title="P&L Trend" subtitle="cumulative profit over time" pro locked onUpgrade={onUpgrade}>{null}</Section>
              )}

              <Section icon={Users} title="Bettor Leaderboard" subtitle="top performers ranked by hit rate">
                <Leaderboard />
              </Section>
            </div>
          )}

          {/* ══════════════════════════════════════════════════════════════════
              TAB 2 — MARKETS
              Drill into which markets, leagues and signal types make money.
          ══════════════════════════════════════════════════════════════════ */}
          {analyticsTab === 'markets' && (
            <div className="space-y-5">
              {data.byTier.length > 0 && (
                <Section icon={Layers} title="League Tier Breakdown" subtitle="Home Over 0.5 performance by league tier">
                  <TierBreakdown rows={data.byTier} />
                </Section>
              )}

              {isPro ? (
                data.byLeague.length > 0 && (
                  <Section icon={Layers} title="League Performance" subtitle="results by competition" pro>
                    <div className="space-y-5">
                      <LeagueInsights rows={data.byLeague} />
                      <ByMarketTable rows={data.byLeague} title="" keyField="league" />
                    </div>
                  </Section>
                )
              ) : (
                <Section icon={Layers} title="League Performance" pro locked onUpgrade={onUpgrade}>{null}</Section>
              )}

              {(data.byConfidence.length > 0 || data.byAgreement.length >= 2) && (
                <Section icon={TrendingUp} title="Signal Quality" subtitle="how each confidence tier and engine agreement type performs">
                  <SignalQualityContent
                    byConfidence={data.byConfidence}
                    byAgreement={data.byAgreement}
                    showFactor={Boolean(insights?.by_confidence?.length)}
                    onApplySignalFilter={onApplySignalFilter}
                  />
                </Section>
              )}

              {isPro && data.bySource?.length > 0 && (
                <Section icon={Zap} title="Pick Sources" subtitle="performance by how a bet was added" pro>
                  <ByMarketTable rows={data.bySource} title="" keyField="source" />
                </Section>
              )}

              {isPro && data.byRule.length > 0 && (
                <Section icon={Zap} title="Signal Rules" subtitle="outcomes by rule trigger" pro>
                  <ByMarketTable rows={data.byRule} title="" keyField="rule_key" />
                </Section>
              )}

              <Section icon={Layers} title="ACCA Performance" subtitle="leg hit rate and ticket hit rate by market and leg count">
                <AccaPerformanceCard />
              </Section>

            </div>
          )}

          {/* ══════════════════════════════════════════════════════════════════
              TAB 3 — STRATEGY
              Model health, staking simulation, and self-learning adjustments.
              Power-user tools — act on what you learn in Overview and Markets.
          ══════════════════════════════════════════════════════════════════ */}
          {analyticsTab === 'strategy' && (
            <div className="space-y-5">
              <Section icon={Coins} title="Staking Plan Simulator" subtitle="flat vs ½ Kelly vs full Kelly equity curves">
                <StakingSimulator dateFrom={dateFrom} dateTo={dateTo} />
              </Section>

              <Section icon={Crosshair} title="Model Calibration" subtitle="does our probability match reality?">
                <CalibrationChart dateFrom={dateFrom} dateTo={dateTo} />
              </Section>

              {/* Engine Health — one home for everything the self-learning loop
                  produces. Sub-tabs replace the former Self-Learning Engine,
                  Model Intelligence and Parameter Hub sections, which overlapped. */}
              {isPro ? (
                <Section icon={Brain} title="Engine Health" subtitle="weights, threshold changes and active parameters" pro>
                  <EngineHealth insights={insights} onApplySignalFilter={onApplySignalFilter} />
                </Section>
              ) : (
                <Section icon={Brain} title="Engine Health" pro locked onUpgrade={onUpgrade}>{null}</Section>
              )}

              {isPro ? (
                <Section icon={TrendingDown} title="AI Loss Analysis" subtitle="diagnose patterns in your settled losses" pro>
                  <LossAnalysisDashboard />
                </Section>
              ) : (
                <Section icon={TrendingDown} title="AI Loss Analysis" pro locked onUpgrade={onUpgrade}>{null}</Section>
              )}
            </div>
          )}

        </>
      )}
    </div>
  )
}

// ── ACCA Performance Card ─────────────────────────────────────────────────────
function AccaPerformanceCard() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('market')

  useEffect(() => {
    setLoading(true)
    fetchAccaPerformance()
      .then(d => setData(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="flex justify-center py-6"><LoadingSpinner /></div>
  if (error) return <p className="text-sm text-red-400">{error}</p>

  const { by_market = [], by_leg_count = [], two_market_combos = [] } = data || {}
  const hasData = by_market.length > 0 || by_leg_count.length > 0

  if (!hasData) {
    return (
      <p className="text-sm text-[var(--text)] opacity-75 py-4 text-center">
        No settled ACCA legs yet — data appears after the first batch of ACCA tickets are resolved.
      </p>
    )
  }

  const TABS = [
    { id: 'market',  label: 'By Market' },
    { id: 'legs',    label: 'By Leg Count' },
    { id: 'combos',  label: 'Combos' },
  ]

  return (
    <div className="space-y-4">
      <div className="flex gap-1 border-b border-[var(--border)]">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={'px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors ' + (
              tab === t.id
                ? 'border-[var(--accent)] text-[var(--accent)]'
                : 'border-transparent text-[var(--text)] opacity-70 hover:opacity-100'
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'market' && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--border)] text-[var(--text)] opacity-70">
                <th className="px-3 py-2 text-left">Market</th>
                <th className="px-3 py-2 text-right">Legs</th>
                <th className="px-3 py-2 text-right">Hit Rate</th>
                <th className="px-3 py-2 text-right">ROI</th>
              </tr>
            </thead>
            <tbody>
              {by_market.map((row, i) => {
                const hrColor = row.hit_rate >= 60 ? 'text-green-400' : row.hit_rate >= 50 ? 'text-yellow-400' : 'text-[var(--text-h)]'
                const roiColor = row.roi >= 5 ? 'text-green-400' : row.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
                return (
                  <tr key={i} className="border-t border-[var(--border)] hover:bg-[var(--code-bg)]">
                    <td className="px-3 py-2 font-medium text-[var(--text-h)]">{row.market}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-[var(--text)]">{row.legs}</td>
                    <td className={'px-3 py-2 text-right tabular-nums font-semibold ' + hrColor}>{row.hit_rate}%</td>
                    <td className={'px-3 py-2 text-right tabular-nums font-semibold ' + roiColor}>{row.roi >= 0 ? '+' : ''}{row.roi}%</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'legs' && (
        <div className="space-y-2">
          {by_leg_count.map((row, i) => {
            const hrColor = row.hit_rate >= 40 ? 'text-green-400' : row.hit_rate >= 25 ? 'text-yellow-400' : 'text-red-400'
            const pct = Math.max(4, row.hit_rate)
            return (
              <div key={i} className="rounded-lg border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-[var(--text-h)]">{row.leg_count}-leg ticket</span>
                  <div className="flex items-center gap-3 text-[11px]">
                    <span className="text-[var(--text)] opacity-80">{row.tickets} tickets · {row.wins}W</span>
                    <span className={'font-mono font-semibold ' + hrColor}>{row.hit_rate}% hit</span>
                  </div>
                </div>
                <div className="h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
                  <div className="h-full rounded-full bg-indigo-500 opacity-70" style={{ width: pct + '%' }} />
                </div>
              </div>
            )
          })}
          <p className="text-[10px] text-[var(--text)] opacity-55 pt-1">
            Ticket win = all legs win. Hit rate drops sharply with each added leg — expected.
          </p>
        </div>
      )}

      {tab === 'combos' && (
        two_market_combos.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--border)] text-[var(--text)] opacity-70">
                  <th className="px-3 py-2 text-left">Market A</th>
                  <th className="px-3 py-2 text-left">Market B</th>
                  <th className="px-3 py-2 text-right">Tickets</th>
                  <th className="px-3 py-2 text-right">Hit Rate</th>
                </tr>
              </thead>
              <tbody>
                {two_market_combos.map((row, i) => {
                  const hrColor = row.hit_rate >= 40 ? 'text-green-400' : row.hit_rate >= 25 ? 'text-yellow-400' : 'text-[var(--text-h)]'
                  return (
                    <tr key={i} className="border-t border-[var(--border)] hover:bg-[var(--code-bg)]">
                      <td className="px-3 py-2 text-[var(--text-h)]">{row.market_a}</td>
                      <td className="px-3 py-2 text-[var(--text-h)]">{row.market_b}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-[var(--text)]">{row.tickets}</td>
                      <td className={'px-3 py-2 text-right tabular-nums font-semibold ' + hrColor}>{row.hit_rate}%</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-[var(--text)] opacity-70 py-4 text-center">
            Need at least 2 tickets with the same two-market pair to show combo data.
          </p>
        )
      )}
    </div>
  )
}

// ── Engine Health — unified self-learning view ───────────────────────────────
// Replaces the former Self-Learning Engine, Model Intelligence and Parameter Hub
// sections, which surfaced overlapping slices of the same self-learning output.
function EngineHealth({ insights, onApplySignalFilter }) {
  const [tab, setTab] = useState('weights')
  const TABS = [
    { id: 'weights',    label: 'Weights' },
    { id: 'thresholds', label: 'Threshold Changes' },
    { id: 'parameters', label: 'Active Parameters' },
  ]
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1 border-b border-[var(--border)]">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={'px-3 py-1.5 text-xs font-medium rounded-t-md border-b-2 transition-colors ' + (
              tab === t.id
                ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--accent-bg,rgba(99,102,241,0.08))]'
                : 'border-transparent text-[var(--text)] opacity-70 hover:opacity-100 hover:text-[var(--text-h)]'
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'weights'    && <ModelInsightsContent insights={insights} />}
      {tab === 'thresholds' && <ModelIntelligenceDashboard />}
      {tab === 'parameters' && <ParameterHub onApplySignalFilter={onApplySignalFilter} />}
    </div>
  )
}

// ── Staking Plan Simulator ────────────────────────────────────────────────────
function StakingSimulator({ dateFrom, dateTo }) {
  const [bets, setBets] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchStakingSimulation({ date_from: dateFrom || undefined, date_to: dateTo || undefined })
      .then(data => setBets(Array.isArray(data) ? data : []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [dateFrom, dateTo])

  if (loading) return <div className="flex justify-center py-8"><LoadingSpinner /></div>
  if (error) return <p className="text-sm text-red-400">{error}</p>
  if (!bets || bets.length === 0) {
    return <p className="text-sm text-[var(--text)] opacity-80 py-4 text-center">No settled bets to simulate. Track and settle picks in the Tracker first.</p>
  }

  // Build 3 equity curves starting from 1000 units
  const START = 1000
  const FLAT_UNIT = 10  // 1% of 1000
  const curves = bets.reduce((acc, bet) => {
    const prev = acc[acc.length - 1]
    const won = bet.result === 'Won'

    // Flat: always bet FLAT_UNIT
    const flatStake = FLAT_UNIT
    const flatPL = won ? flatStake * (bet.odds - 1) : -flatStake
    const flatNew = Math.max(0, prev.flat + flatPL)

    // Half Kelly: use recommended_stake_pct if available, else implied kelly
    const impliedProb = bet.odds > 1 ? 1 / bet.odds : 0
    const kellyFull = bet.odds > 1 ? Math.max(0, (impliedProb * bet.odds - 1) / (bet.odds - 1)) : 0
    const halfK = bet.stake_pct != null ? bet.stake_pct * 0.5 : kellyFull * 0.5
    const halfKStake = Math.max(1, prev.halfKelly * halfK)
    const halfKPL = won ? halfKStake * (bet.odds - 1) : -halfKStake
    const halfKNew = Math.max(0, prev.halfKelly + halfKPL)

    // Full Kelly
    const fullK = bet.stake_pct != null ? bet.stake_pct : kellyFull
    const fullKStake = Math.max(1, prev.kelly * fullK)
    const fullKPL = won ? fullKStake * (bet.odds - 1) : -fullKStake
    const fullKNew = Math.max(0, prev.kelly + fullKPL)

    acc.push({
      label: bet.date || '',
      flat: Math.round(flatNew),
      halfKelly: Math.round(halfKNew),
      kelly: Math.round(fullKNew),
    })
    return acc
  }, [{ label: 'Start', flat: START, halfKelly: START, kelly: START }])

  const finalFlat = curves[curves.length - 1].flat
  const finalHalf = curves[curves.length - 1].halfKelly
  const finalKelly = curves[curves.length - 1].kelly

  const roiColor = v => v >= START ? 'text-emerald-400' : 'text-red-400'

  return (
    <div className="space-y-4">
      {/* Summary pills */}
      <div className="grid grid-cols-3 gap-2">
        {[
          { label: 'Flat (1%)', value: finalFlat, color: '#6366f1' },
          { label: '½ Kelly',   value: finalHalf, color: '#f59e0b' },
          { label: 'Full Kelly',value: finalKelly, color: '#10b981' },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-lg bg-[var(--code-bg)] p-2.5 text-center">
            <p className="text-[10px] text-[var(--text)] opacity-55 mb-0.5">{label}</p>
            <p className={`text-sm font-bold tabular-nums ${roiColor(value)}`}>
              {value >= START ? '+' : ''}{((value / START - 1) * 100).toFixed(1)}%
            </p>
            <p className="text-[10px] text-[var(--text)] opacity-65 tabular-nums">{value.toLocaleString()} u</p>
          </div>
        ))}
      </div>

      {/* Chart */}
      <div style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={curves} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.4} />
            <XAxis dataKey="label" tick={false} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 10, fill: 'var(--text)', opacity: 0.5 }} axisLine={false} tickLine={false} />
            <Tooltip
              contentStyle={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 11 }}
              formatter={(val, name) => [`${val.toLocaleString()} u`, name]}
            />
            <ReferenceLine y={START} stroke="var(--border)" strokeDasharray="4 4" />
            <Line type="monotone" dataKey="flat"      stroke="#6366f1" strokeWidth={2} dot={false} name="Flat 1%" />
            <Line type="monotone" dataKey="halfKelly" stroke="#f59e0b" strokeWidth={2} dot={false} name="½ Kelly" />
            <Line type="monotone" dataKey="kelly"     stroke="#10b981" strokeWidth={2} dot={false} name="Full Kelly" />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="text-[10px] text-[var(--text)] opacity-45 text-center">
        All curves start at 1,000 units · dashed line = break-even · {bets.length} settled bets
      </p>
    </div>
  )
}

// ── Model Calibration Chart ───────────────────────────────────────────────────
function CalibrationChart({ dateFrom, dateTo }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchProbabilityCalibration({ date_from: dateFrom || undefined, date_to: dateTo || undefined })
      .then(d => setData(Array.isArray(d) ? d : []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [dateFrom, dateTo])

  if (loading) return <div className="flex justify-center py-8"><LoadingSpinner /></div>
  if (error) return <p className="text-sm text-red-400">{error}</p>
  if (!data || data.length === 0) {
    return <p className="text-sm text-[var(--text)] opacity-80 py-4 text-center">Need more settled bets to build a calibration curve.</p>
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-[var(--text)] opacity-55">
        A perfectly calibrated model sits on the diagonal — if we say 70%, the outcome happens 70% of the time.
        Green bars = model underestimates (safer than shown). Red bars = overconfident.
      </p>

      {/* Bars: actual win rate vs model prob */}
      <div style={{ height: 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.4} />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: 'var(--text)', opacity: 0.6 }} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: 'var(--text)', opacity: 0.5 }} axisLine={false} tickLine={false} tickFormatter={v => `${v}%`} />
            <Tooltip
              contentStyle={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 11 }}
              formatter={(val, name) => [`${val}%`, name]}
            />
            <Bar dataKey="avg_model_prob" name="Model Prob" fill="#6366f1" opacity={0.4} radius={[4, 4, 0, 0]} />
            <Bar dataKey="actual_win_rate" name="Actual Win Rate" radius={[4, 4, 0, 0]}>
              {data.map((entry, i) => (
                <Cell
                  key={i}
                  fill={entry.calibration_error > 5 ? '#ef4444' : entry.calibration_error < -5 ? '#10b981' : '#f59e0b'}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[var(--text)] opacity-80 border-b border-[var(--border)]">
              <th className="px-3 py-2 text-left">Prob Bucket</th>
              <th className="px-3 py-2 text-right">Model</th>
              <th className="px-3 py-2 text-right">Actual</th>
              <th className="px-3 py-2 text-right">Error</th>
              <th className="px-3 py-2 text-right">Bets</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => {
              const err = row.calibration_error
              const errColor = err > 5 ? 'text-red-400' : err < -5 ? 'text-emerald-400' : 'text-[var(--text-h)]'
              return (
                <tr key={i} className="border-b border-[var(--border)] last:border-0">
                  <td className="px-3 py-2 text-[var(--text-h)] font-medium">{row.label}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-[var(--text)]">{row.avg_model_prob}%</td>
                  <td className="px-3 py-2 text-right tabular-nums text-[var(--text-h)] font-semibold">{row.actual_win_rate}%</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-semibold ${errColor}`}>
                    {err >= 0 ? '+' : ''}{err}pp
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-[var(--text)] opacity-80">{row.sample_size}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-[var(--text)] opacity-65">
        Error = Actual − Model. Positive = overconfident (actual worse than predicted). Negative = underestimating (actual better).
      </p>
    </div>
  )
}
