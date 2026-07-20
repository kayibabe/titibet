import { useState } from 'react'
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'
import { fmtPL, fmtPLCompact } from '../../utils/format'

function WinRateBar({ rate }) {
  const pct = Math.min(100, Math.max(0, rate ?? 0))
  const color = pct >= 60 ? 'bg-green-400' : pct >= 45 ? 'bg-yellow-400' : 'bg-red-400'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-14 h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span
        className="text-xs font-semibold tabular-nums w-9 text-right"
        style={{ color: pct >= 60 ? '#4ade80' : pct >= 45 ? '#facc15' : '#f87171' }}
      >
        {pct.toFixed(1)}%
      </span>
    </div>
  )
}

function SortIcon({ active, dir }) {
  if (!active) return <ChevronsUpDown size={11} className="opacity-30" />
  return dir === 'desc'
    ? <ChevronDown size={11} className="text-[var(--accent)]" />
    : <ChevronUp size={11} className="text-[var(--accent)]" />
}

export default function MarketStatsTable({ summary }) {
  const [sortCol, setSortCol] = useState('roi')
  const [sortDir, setSortDir] = useState('desc')

  if (!summary) return null

  const {
    total_bets, total,
    wins = 0, losses = 0,
    hit_rate, roi, total_profit, avg_odds,
    by_market = [],
  } = summary
  const totalBets = total_bets ?? total ?? 0

  function toggleSort(col) {
    if (sortCol === col) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortCol(col); setSortDir('desc') }
  }

  const sortedMarkets = [...by_market].sort((a, b) => {
    const va = a[sortCol] ?? (sortCol === 'market' ? '' : -Infinity)
    const vb = b[sortCol] ?? (sortCol === 'market' ? '' : -Infinity)
    if (typeof va === 'string') return sortDir === 'desc' ? vb.localeCompare(va) : va.localeCompare(vb)
    return sortDir === 'desc' ? vb - va : va - vb
  })

  const thRight = (col) =>
    `px-3 py-2 text-right font-medium cursor-pointer select-none whitespace-nowrap transition-colors hover:text-[var(--text-h)] ${sortCol === col ? 'text-[var(--accent)]' : 'text-[var(--text)] opacity-75'}`

  const roiColor = roi >= 20 ? 'text-green-400' : roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
  const plColor  = (total_profit ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
  const hrColor  = hit_rate >= 60 ? 'text-green-400' : hit_rate >= 45 ? 'text-[var(--text-h)]' : 'text-red-400'

  const kpis = [
    {
      label: 'Total Bets',
      value: totalBets,
      sub:   `${wins}W · ${losses}L`,
    },
    {
      label: 'Hit Rate',
      value: hit_rate != null ? `${hit_rate.toFixed(1)}%` : '—',
      color: hrColor,
    },
    {
      label: 'Avg Odds',
      value: avg_odds != null ? avg_odds.toFixed(2) : '—',
    },
    {
      label: 'ROI',
      value: roi != null ? `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%` : '—',
      color: roiColor,
    },
    {
      label:     'Net P&L',
      value:     total_profit != null ? fmtPLCompact(total_profit) : '—',
      fullValue: total_profit != null ? fmtPL(total_profit) : null,
      color:     plColor,
    },
  ]

  return (
    <div className="space-y-5">
      {/* KPI summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {kpis.map(card => (
          <div key={card.label} className="rounded-lg border border-[var(--border)] bg-[var(--code-bg)] px-3 py-2.5">
            <div className="text-[10px] font-semibold text-[var(--text)] opacity-65 uppercase tracking-wide mb-1">
              {card.label}
            </div>
            <div
              className={`text-xl font-bold tabular-nums leading-tight ${card.color ?? 'text-[var(--text-h)]'}`}
              title={card.fullValue ?? undefined}
            >
              {card.value ?? '—'}
            </div>
            {card.sub && (
              <div className="text-[10px] text-[var(--text)] opacity-70 mt-0.5">{card.sub}</div>
            )}
          </div>
        ))}
      </div>

      {/* Per-market sortable breakdown */}
      {sortedMarkets.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold text-[var(--text-h)]">By Market</span>
            <span className="text-[10px] text-[var(--text)] opacity-70">
              {by_market.length} market{by_market.length !== 1 ? 's' : ''}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--border)]">
                  <th
                    className="px-3 py-2 text-left font-medium cursor-pointer select-none hover:text-[var(--text-h)] transition-colors text-[var(--text)] opacity-75"
                    onClick={() => toggleSort('market')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Market <SortIcon active={sortCol === 'market'} dir={sortDir} />
                    </span>
                  </th>
                  <th className={thRight('total')} onClick={() => toggleSort('total')}>
                    <span className="inline-flex items-center gap-1 justify-end">
                      Bets <SortIcon active={sortCol === 'total'} dir={sortDir} />
                    </span>
                  </th>
                  <th className="px-3 py-2 text-right font-medium text-[var(--text)] opacity-75 whitespace-nowrap">
                    W / L
                  </th>
                  <th
                    className="px-3 py-2 text-left font-medium text-[var(--text)] opacity-75 pl-4 min-w-[140px] cursor-pointer select-none hover:text-[var(--text-h)] transition-colors"
                    onClick={() => toggleSort('hit_rate')}
                  >
                    <span className="inline-flex items-center gap-1">
                      Hit Rate <SortIcon active={sortCol === 'hit_rate'} dir={sortDir} />
                    </span>
                  </th>
                  <th className={thRight('avg_odds')} onClick={() => toggleSort('avg_odds')}>
                    <span className="inline-flex items-center gap-1 justify-end">
                      Avg Odds <SortIcon active={sortCol === 'avg_odds'} dir={sortDir} />
                    </span>
                  </th>
                  <th className={thRight('roi')} onClick={() => toggleSort('roi')}>
                    <span className="inline-flex items-center gap-1 justify-end">
                      ROI <SortIcon active={sortCol === 'roi'} dir={sortDir} />
                    </span>
                  </th>
                  <th className={thRight('profit')} onClick={() => toggleSort('profit')}>
                    <span className="inline-flex items-center gap-1 justify-end">
                      P&L <SortIcon active={sortCol === 'profit'} dir={sortDir} />
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedMarkets.map((row, i) => {
                  const rowRoi = row.roi ?? 0
                  const rowPl  = row.profit ?? 0
                  const roiCls = rowRoi >= 20 ? 'text-green-400' : rowRoi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
                  const plCls  = rowPl >= 0 ? 'text-green-400' : 'text-red-400'
                  return (
                    <tr key={i} className="border-t border-[var(--border)] hover:bg-[var(--code-bg)] transition-colors">
                      <td className="px-3 py-2.5 font-medium text-[var(--text-h)] max-w-[180px] truncate" title={row.market}>
                        {row.market}
                      </td>
                      <td className="px-3 py-2.5 text-right text-[var(--text)]">
                        {row.total ?? row.count ?? 0}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <span className="text-green-400">{row.wins ?? 0}</span>
                        <span className="text-[var(--text)] opacity-65 mx-1">/</span>
                        <span className="text-red-400">{row.losses ?? 0}</span>
                      </td>
                      <td className="px-3 py-2.5 pl-4">
                        <WinRateBar rate={row.hit_rate} />
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-[var(--text)] tabular-nums">
                        {row.avg_odds != null ? row.avg_odds.toFixed(2) : '—'}
                      </td>
                      <td className={`px-3 py-2.5 text-right font-semibold tabular-nums ${roiCls}`}>
                        {rowRoi >= 0 ? '+' : ''}{rowRoi.toFixed(1)}%
                      </td>
                      <td
                        className={`px-3 py-2.5 text-right font-mono tabular-nums ${plCls}`}
                        title={fmtPL(rowPl)}
                      >
                        {fmtPLCompact(rowPl)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
