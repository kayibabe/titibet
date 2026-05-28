/**
 * PLChart — cumulative P&L sparkline for the tracker bets tab.
 *
 * Takes the full bets array, filters to settled bets, sorts by event_date
 * (then created_at as tie-break), and renders a running cumulative P&L line
 * using recharts AreaChart.
 */
import { useMemo } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { fmtK } from '../../utils/format'

function buildCurve(bets) {
  const settled = bets
    .filter(b => b.result_status !== 'Pending' && b.profit_loss != null)
    .sort((a, b) => {
      const da = a.event_date || (a.created_at ? String(a.created_at).slice(0, 10) : '0000')
      const db_ = b.event_date || (b.created_at ? String(b.created_at).slice(0, 10) : '0000')
      return da < db_ ? -1 : da > db_ ? 1 : 0
    })

  if (settled.length === 0) return []

  let running = 0
  return settled.map((b, i) => {
    running += b.profit_loss
    const label = b.event_date
      ? new Date(`${b.event_date}T00:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
      : `#${i + 1}`
    return {
      label,
      pl: round2(running),
      match: b.match_name || '',
      market: b.market_type || '',
      result: b.result_status,
      thisPL: b.profit_loss,
    }
  })
}

function round2(v) { return Math.round(v * 100) / 100 }

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  const plColor = d.pl >= 0 ? '#4ade80' : '#f87171'
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 shadow-lg text-xs space-y-0.5">
      <p className="font-medium text-[var(--text-h)]">{d.label}</p>
      {d.match && <p className="text-[var(--text)] opacity-75 truncate max-w-[180px]">{d.match}</p>}
      {d.market && <p className="text-[var(--text)] opacity-80">{d.market} · {d.result}</p>}
      <p>Bet P/L: <span style={{ color: d.thisPL >= 0 ? '#4ade80' : '#f87171' }} className="font-semibold font-mono">
        {d.thisPL >= 0 ? '+' : ''}{fmtK(d.thisPL)}
      </span></p>
      <p>Running: <span style={{ color: plColor }} className="font-bold font-mono">
        {d.pl >= 0 ? '+' : ''}{fmtK(d.pl)}
      </span></p>
    </div>
  )
}

export default function PLChart({ bets }) {
  const data = useMemo(() => buildCurve(bets), [bets])

  if (data.length < 2) return null   // need at least 2 points for a meaningful line

  const finalPL = data[data.length - 1].pl
  const isPositive = finalPL >= 0
  const color = isPositive ? '#4ade80' : '#f87171'

  // Thin sparkline mode when few data points; fuller chart when more
  const chartHeight = data.length >= 10 ? 120 : 80

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 pt-3 pb-2">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-[var(--text-h)]">Cumulative P&amp;L</span>
        <span className={`text-sm font-bold font-mono ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
          {finalPL >= 0 ? '+' : ''}{fmtK(finalPL)}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={chartHeight}>
        <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="plGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={color} stopOpacity={0.25} />
              <stop offset="95%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <XAxis dataKey="label" hide={data.length > 20} tick={{ fontSize: 9, fill: 'var(--text)', opacity: 0.5 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
          <YAxis hide tickFormatter={fmtK} />
          <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" />
          <Tooltip content={<CustomTooltip />} />
          <Area
            type="monotone"
            dataKey="pl"
            stroke={color}
            strokeWidth={2}
            fill="url(#plGrad)"
            dot={false}
            activeDot={{ r: 4, strokeWidth: 0, fill: color }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
