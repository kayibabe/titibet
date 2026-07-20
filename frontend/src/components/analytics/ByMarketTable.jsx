import { useState } from 'react'
import { ChevronUp, ChevronDown, ChevronsUpDown, TrendingUp } from 'lucide-react'
import { fmtPL, fmtPLCompact } from '../../utils/format'

function WinRateBar({ rate }) {
  const pct = Math.min(100, Math.max(0, rate ?? 0))
  const color = pct >= 60 ? 'bg-green-400' : pct >= 45 ? 'bg-yellow-400' : 'bg-red-400'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-12 h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums w-9 text-right"
            style={{ color: pct >= 60 ? '#4ade80' : pct >= 45 ? '#facc15' : '#f87171' }}>
        {pct.toFixed(1)}%
      </span>
    </div>
  )
}

function SortIcon({ col, active, dir }) {
  if (active) {
    return dir === 'desc'
      ? <ChevronDown size={11} className="text-[var(--accent)]" />
      : <ChevronUp size={11} className="text-[var(--accent)]" />
  }
  return <ChevronsUpDown size={11} className="opacity-30" />
}

export default function ByMarketTable({ rows = [], title = 'By Market', keyField = 'market', onFilterSignals }) {
  const [sortCol, setSortCol] = useState('roi')
  const [sortDir, setSortDir] = useState('desc')

  function toggleSort(col) {
    if (sortCol === col) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortCol(col)
      setSortDir('desc')
    }
  }

  const sorted = [...rows].sort((a, b) => {
    const va = a[sortCol] ?? -Infinity
    const vb = b[sortCol] ?? -Infinity
    return sortDir === 'desc' ? vb - va : va - vb
  })

  const colHeader = keyField === 'league' ? 'League' : keyField === 'rule_key' ? 'Rule' : 'Market'

  if (!rows.length) {
    return (
      <div>
        <h3 className="text-sm font-semibold text-[var(--text-h)] mb-2">{title}</h3>
        <p className="text-xs text-[var(--text)] opacity-65 py-4 text-center">No data for this period</p>
      </div>
    )
  }

  const thCls = (col) =>
    `px-3 py-2 text-right font-medium cursor-pointer select-none hover:text-[var(--text-h)] transition-colors whitespace-nowrap ${sortCol === col ? 'text-[var(--accent)]' : 'text-[var(--text)] opacity-75'}`

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-[var(--text-h)]">{title}</h3>
        <span className="text-[10px] text-[var(--text)] opacity-70">{rows.length} {colHeader.toLowerCase()}{rows.length !== 1 ? 's' : ''}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[var(--border)]">
              <th className="px-3 py-2 text-left font-medium text-[var(--text)] opacity-75" colSpan={onFilterSignals && keyField === 'market' ? 2 : 1}>{colHeader}</th>
              <th className={thCls('bets')} onClick={() => toggleSort('bets')}>
                <span className="inline-flex items-center gap-1 justify-end">Bets <SortIcon col="bets" active={sortCol==='bets'} dir={sortDir} /></span>
              </th>
              <th className="px-3 py-2 text-right font-medium text-[var(--text)] opacity-75">W / L</th>
              <th className="px-3 py-2 text-left font-medium text-[var(--text)] opacity-75 pl-4 min-w-[130px]">
                <span className="inline-flex items-center gap-1 cursor-pointer hover:text-[var(--text-h)]"
                      onClick={() => toggleSort('win_rate')}>
                  Hit Rate <SortIcon col="win_rate" active={sortCol==='win_rate'} dir={sortDir} />
                </span>
              </th>
              <th className={thCls('avg_odds')} onClick={() => toggleSort('avg_odds')}>
                <span className="inline-flex items-center gap-1 justify-end">Avg Odds <SortIcon col="avg_odds" active={sortCol==='avg_odds'} dir={sortDir} /></span>
              </th>
              <th className={thCls('roi')} onClick={() => toggleSort('roi')}>
                <span className="inline-flex items-center gap-1 justify-end">ROI <SortIcon col="roi" active={sortCol==='roi'} dir={sortDir} /></span>
              </th>
              <th className={thCls('profit_loss')} onClick={() => toggleSort('profit_loss')}>
                <span className="inline-flex items-center gap-1 justify-end">P&L <SortIcon col="profit_loss" active={sortCol==='profit_loss'} dir={sortDir} /></span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => {
              const pl_val = row.profit_loss ?? row.total_profit_loss ?? 0
              const roiColor = row.roi >= 20 ? 'text-green-400' : row.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
              const plColor  = pl_val >= 0 ? 'text-green-400' : 'text-red-400'
              const label    = row[keyField] || row.market || row.league || row.rule_key || '—'

              return (
                <tr key={i} className="border-t border-[var(--border)] hover:bg-[var(--code-bg)] transition-colors group">
                  <td className="px-3 py-2.5 font-medium text-[var(--text-h)] max-w-[160px] truncate" title={label}>
                    {label}
                  </td>
                  {onFilterSignals && keyField === 'market' && (
                    <td className="px-1 py-2.5 w-6">
                      <button
                        onClick={() => onFilterSignals({ market: label, label: `Market: ${label}` })}
                        title={`Filter signals to ${label}`}
                        className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center text-[var(--accent)] hover:text-[var(--accent)] p-0.5 rounded"
                      >
                        <TrendingUp size={11} />
                      </button>
                    </td>
                  )}
                  <td className="px-3 py-2.5 text-right text-[var(--text)]">{row.bets ?? '—'}</td>
                  <td className="px-3 py-2.5 text-right">
                    <span className="text-green-400">{row.wins ?? 0}</span>
                    <span className="text-[var(--text)] opacity-65 mx-1">/</span>
                    <span className="text-red-400">{row.losses ?? 0}</span>
                  </td>
                  <td className="px-3 py-2.5 pl-4">
                    <WinRateBar rate={row.win_rate} />
                  </td>
                  <td className="px-3 py-2.5 text-right text-[var(--text)] tabular-nums">
                    {row.avg_odds != null ? row.avg_odds.toFixed(2) : '—'}
                  </td>
                  <td className={`px-3 py-2.5 text-right font-semibold tabular-nums ${roiColor}`}>
                    {row.roi != null ? `${row.roi >= 0 ? '+' : ''}${row.roi.toFixed(1)}%` : '—'}
                  </td>
                  <td className={`px-3 py-2.5 text-right font-mono tabular-nums ${plColor}`} title={fmtPL(pl_val)}>{fmtPLCompact(pl_val)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
