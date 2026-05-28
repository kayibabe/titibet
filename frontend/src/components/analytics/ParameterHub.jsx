/**
 * ParameterHub — shows which markets and leagues are performing well (active),
 * which are underperforming (suspended), and which need more data (monitoring).
 *
 * Suspended parameters continue to generate signals — the hub makes the
 * performance picture visible so users can choose to focus on active ones.
 * When the user clicks "Filter signals →" it fires onApplySignalFilter so the
 * signals page zooms in on that market / league.
 */
import { useState, useEffect } from 'react'
import { TrendingUp, TrendingDown, Eye, ChevronDown, ChevronRight, ArrowRight } from 'lucide-react'
import { fetchParameterStatus } from '../../api/analytics'

const STATUS_CONFIG = {
  active: {
    label:      'Active',
    icon:       TrendingUp,
    headerCls:  'text-emerald-400',
    rowCls:     'hover:bg-emerald-500/5',
    badgeCls:   'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
    barCls:     'bg-emerald-500',
    dot:        'bg-emerald-400',
  },
  suspended: {
    label:      'Suspended',
    icon:       TrendingDown,
    headerCls:  'text-red-400',
    rowCls:     'hover:bg-red-500/5',
    badgeCls:   'bg-red-500/15 text-red-300 border-red-500/30',
    barCls:     'bg-red-500',
    dot:        'bg-red-400',
  },
  monitoring: {
    label:      'Monitoring',
    icon:       Eye,
    headerCls:  'text-[var(--text)] opacity-80',
    rowCls:     'hover:bg-[var(--code-bg)]',
    badgeCls:   'bg-[var(--border)] text-[var(--text)] border-[var(--border)]',
    barCls:     'bg-[var(--text)] opacity-30',
    dot:        'bg-[var(--text)] opacity-65',
  },
}

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.monitoring
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded border ${cfg.badgeCls}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  )
}

function roiColor(roi) {
  if (roi >= 10) return 'text-emerald-400 font-semibold'
  if (roi >= 0)  return 'text-[var(--text-h)]'
  if (roi >= -10) return 'text-amber-400'
  return 'text-red-400 font-semibold'
}

function hitColor(wr) {
  if (wr >= 60) return 'text-emerald-400'
  if (wr >= 50) return 'text-[var(--text-h)]'
  return 'text-red-400'
}

function ParameterTable({ rows, type, onApplySignalFilter, showFilterButton }) {
  const [monitoringOpen, setMonitoringOpen] = useState(false)

  const active    = rows.filter(r => r.status === 'active')
  const suspended = rows.filter(r => r.status === 'suspended')
  const monitoring = rows.filter(r => r.status === 'monitoring')

  if (rows.length === 0) {
    return (
      <p className="text-xs text-[var(--text)] opacity-80 py-4 text-center">
        No tracked bets for {type} yet. Start tracking picks in the Bet Tracker
        and settle them — the hub will populate as history builds.
      </p>
    )
  }

  function Row({ row }) {
    const cfg = STATUS_CONFIG[row.status] || STATUS_CONFIG.monitoring
    const filterField = type === 'markets' ? { market: row.parameter } : { league: row.parameter }

    return (
      <tr className={`border-t border-[var(--border)] transition-colors ${cfg.rowCls}`}>
        <td className="px-3 py-2.5">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-[var(--text-h)] truncate max-w-[160px]" title={row.parameter}>
              {row.parameter}
            </span>
            <StatusBadge status={row.status} />
          </div>
          <p className="text-[10px] text-[var(--text)] opacity-70 mt-0.5 leading-snug">{row.reason}</p>
        </td>
        <td className="px-3 py-2.5 text-right tabular-nums text-xs text-[var(--text)]">{row.settled}</td>
        <td className={`px-3 py-2.5 text-right tabular-nums text-xs ${hitColor(row.win_rate)}`}>
          {row.win_rate.toFixed(1)}%
        </td>
        <td className={`px-3 py-2.5 text-right tabular-nums text-xs ${roiColor(row.roi)}`}>
          {row.roi >= 0 ? '+' : ''}{row.roi.toFixed(1)}%
        </td>
        <td className="px-3 py-2.5 text-right tabular-nums text-xs">
          <span className={row.profit_loss >= 0 ? 'text-emerald-400' : 'text-red-400'}>
            {row.profit_loss >= 0 ? '+' : ''}{row.profit_loss.toFixed(1)}
          </span>
        </td>
        {showFilterButton && (
          <td className="px-3 py-2.5 text-right">
            {row.status !== 'monitoring' && onApplySignalFilter && (
              <button
                onClick={() => onApplySignalFilter({
                  ...filterField,
                  label: `${type === 'markets' ? 'Market' : 'League'}: ${row.parameter}`,
                })}
                className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-[var(--accent)] opacity-70 hover:opacity-100 transition-opacity"
                title="Filter signals to this parameter"
              >
                Signals <ArrowRight size={10} />
              </button>
            )}
          </td>
        )}
      </tr>
    )
  }

  const thead = (
    <thead>
      <tr className="border-b border-[var(--border)] text-[var(--text)] opacity-80">
        <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wide">
          {type === 'markets' ? 'Market' : 'League'}
        </th>
        <th className="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wide">Bets</th>
        <th className="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wide">Hit%</th>
        <th className="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wide">ROI</th>
        <th className="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wide">P/L</th>
        {showFilterButton && <th className="px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wide" />}
      </tr>
    </thead>
  )

  return (
    <div className="space-y-4">
      {/* Active */}
      {active.length > 0 && (
        <div className="rounded-lg border border-emerald-500/20 overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 bg-emerald-500/8 border-b border-emerald-500/20">
            <TrendingUp size={12} className="text-emerald-400 shrink-0" />
            <span className="text-[11px] font-bold text-emerald-400 uppercase tracking-wide">Active ({active.length})</span>
            <span className="text-[10px] text-emerald-400 opacity-80 ml-auto">Prioritised in signals</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-xs">
              {thead}
              <tbody>{active.map((r, i) => <Row key={i} row={r} />)}</tbody>
            </table>
          </div>
        </div>
      )}

      {/* Suspended */}
      {suspended.length > 0 && (
        <div className="rounded-lg border border-red-500/20 overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 bg-red-500/8 border-b border-red-500/20">
            <TrendingDown size={12} className="text-red-400 shrink-0" />
            <span className="text-[11px] font-bold text-red-400 uppercase tracking-wide">Suspended ({suspended.length})</span>
            <span className="text-[10px] text-red-400 opacity-80 ml-auto">Still tracked · deprioritised</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-xs">
              {thead}
              <tbody>{suspended.map((r, i) => <Row key={i} row={r} />)}</tbody>
            </table>
          </div>
        </div>
      )}

      {/* Monitoring (collapsible) */}
      {monitoring.length > 0 && (
        <div className="rounded-lg border border-[var(--border)] overflow-hidden">
          <button
            onClick={() => setMonitoringOpen(o => !o)}
            className="w-full flex items-center gap-2 px-3 py-2 bg-[var(--code-bg)] border-b border-[var(--border)] hover:bg-[var(--border)] transition-colors"
          >
            <Eye size={12} className="text-[var(--text)] opacity-70 shrink-0" />
            <span className="text-[11px] font-semibold text-[var(--text)] opacity-80 uppercase tracking-wide">
              Monitoring ({monitoring.length}) — building data
            </span>
            {monitoringOpen
              ? <ChevronDown size={12} className="ml-auto text-[var(--text)] opacity-65" />
              : <ChevronRight size={12} className="ml-auto text-[var(--text)] opacity-65" />}
          </button>
          {monitoringOpen && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] text-xs">
                {thead}
                <tbody>{monitoring.map((r, i) => <Row key={i} row={r} />)}</tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function ParameterHub({ onApplySignalFilter }) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)
  const [tab,     setTab]     = useState('markets')

  useEffect(() => {
    fetchParameterStatus()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="py-8 flex justify-center">
        <span className="text-xs text-[var(--text)] opacity-70 animate-pulse">Loading parameter data…</span>
      </div>
    )
  }

  if (error || !data) {
    return (
      <p className="text-xs text-[var(--text)] opacity-80 py-4">
        Could not load parameter status — make sure the backend is running and you have tracked bets.
      </p>
    )
  }

  const { markets = [], leagues = [], thresholds = {}, summary = {} } = data
  const rows = tab === 'markets' ? markets : leagues

  const hasAny = markets.length > 0 || leagues.length > 0
  if (!hasAny) {
    return (
      <p className="text-xs text-[var(--text)] opacity-80 py-4 text-center">
        No bet history yet. Track and settle picks in the Bet Tracker — this hub
        updates automatically as you build a track record.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {/* Summary chips */}
      {(summary.active_markets > 0 || summary.suspended_markets > 0) && (
        <div className="flex flex-wrap gap-2">
          {summary.active_markets > 0 && (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 text-[11px] font-semibold text-emerald-300">
              <TrendingUp size={11} />
              {summary.active_markets} active market{summary.active_markets !== 1 ? 's' : ''}
            </span>
          )}
          {summary.suspended_markets > 0 && (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-red-500/30 bg-red-500/10 text-[11px] font-semibold text-red-300">
              <TrendingDown size={11} />
              {summary.suspended_markets} suspended
            </span>
          )}
          {summary.active_leagues > 0 && (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 text-[11px] font-semibold text-emerald-300">
              <TrendingUp size={11} />
              {summary.active_leagues} active league{summary.active_leagues !== 1 ? 's' : ''}
            </span>
          )}
        </div>
      )}

      {/* Tab toggle */}
      <div className="flex gap-1 border-b border-[var(--border)]">
        {[{ id: 'markets', label: 'Markets' }, { id: 'leagues', label: 'Leagues' }].map(t => (
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

      {/* Tables */}
      <ParameterTable
        rows={rows}
        type={tab}
        onApplySignalFilter={onApplySignalFilter}
        showFilterButton={tab === 'markets'}
      />

      {/* Promotion / demotion rules */}
      <div className="rounded-lg bg-[var(--code-bg)] border border-[var(--border)] px-3 py-2.5 text-[10px] text-[var(--text)] opacity-70 space-y-1">
        <p className="font-semibold text-[var(--text-h)] opacity-80 mb-1">Promotion / Demotion Rules</p>
        <p>
          <span className="text-emerald-400 font-semibold">Promotes to Active</span>
          {' — '}≥{thresholds.active_min_bets} settled bets · ROI ≥ +{thresholds.active_min_roi}% · hit rate ≥ {thresholds.active_min_hit_rate}%
        </p>
        <p>
          <span className="text-red-400 font-semibold">Suspended</span>
          {' — '}≥{thresholds.suspend_min_bets} settled bets · ROI ≤ {thresholds.suspend_max_roi}%
        </p>
        <p className="opacity-80">Monitoring = not enough data yet, or performance between these thresholds. All parameters continue generating signals — suspension only affects priority in the signals feed.</p>
      </div>
    </div>
  )
}
