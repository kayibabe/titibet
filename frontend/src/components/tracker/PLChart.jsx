import { useMemo } from 'react'
import {
  ComposedChart, Bar, Cell, Line,
  XAxis, YAxis, Tooltip, ReferenceLine,
  ResponsiveContainer,
} from 'recharts'
import { fmtK } from '../../utils/format'
import useTier from '../../hooks/useTier'

const round2 = v => Math.round(v * 100) / 100

function buildData(bets) {
  const settled = bets.filter(
    b => b.result_status !== 'Pending' && b.profit_loss != null && b.event_date
  )

  // Aggregate per date
  const byDate = {}
  for (const b of settled) {
    if (!byDate[b.event_date]) byDate[b.event_date] = { pl: 0, clvSum: 0, clvCount: 0 }
    byDate[b.event_date].pl += b.profit_loss
    if (b.clv_pct != null) {
      byDate[b.event_date].clvSum += b.clv_pct
      byDate[b.event_date].clvCount += 1
    }
  }

  const dates = Object.keys(byDate).sort()
  let cumul = 0
  return dates.map(date => {
    const { pl, clvSum, clvCount } = byDate[date]
    cumul += pl
    const d = new Date(`${date}T00:00:00`)
    return {
      label: d.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit' }).replace('/', '-'),
      dailyPL: round2(pl),
      cumPL: round2(cumul),
      avgCLV: clvCount > 0 ? round2(clvSum / clvCount) : null,
    }
  })
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const daily = payload.find(p => p.dataKey === 'dailyPL')?.value
  const cumul  = payload.find(p => p.dataKey === 'cumPL')?.value
  const clv    = payload.find(p => p.dataKey === 'avgCLV')?.value
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-xs space-y-0.5 shadow-lg">
      <p className="font-semibold text-[var(--text-h)] mb-1">{label}</p>
      {daily != null && (
        <p className="text-[var(--text)]">
          Daily P&amp;L:{' '}
          <span className={`font-semibold font-mono ${daily >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {daily >= 0 ? '+' : ''}{fmtK(daily)}
          </span>
        </p>
      )}
      {cumul != null && (
        <p className="text-[var(--text)]">
          Cumulative:{' '}
          <span className={`font-semibold font-mono ${cumul >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {cumul >= 0 ? '+' : ''}{fmtK(cumul)}
          </span>
        </p>
      )}
      {clv != null && (
        <p className="text-[var(--text)]">
          Avg CLV:{' '}
          <span className={`font-semibold font-mono ${clv >= 0 ? 'text-violet-400' : 'text-red-400'}`}>
            {clv >= 0 ? '+' : ''}{clv.toFixed(1)}%
          </span>
        </p>
      )}
    </div>
  )
}

function CustomLegend() {
  return (
    <div className="flex items-center justify-center gap-5 mt-2 text-[10px] text-[var(--text)] opacity-60">
      <span className="flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-green-400 opacity-80" />
        Daily P&amp;L
      </span>
      <span className="flex items-center gap-1.5">
        <svg width="18" height="6" style={{ display: 'inline' }}>
          <line x1="0" y1="3" x2="18" y2="3" stroke="#4ade80" strokeWidth="2" strokeLinecap="round" />
        </svg>
        Cumulative
      </span>
      <span className="flex items-center gap-1.5">
        <svg width="18" height="6" style={{ display: 'inline' }}>
          <line x1="0" y1="3" x2="18" y2="3" stroke="#a78bfa" strokeWidth="1.5" strokeDasharray="4 2" strokeLinecap="round" />
        </svg>
        Avg CLV
      </span>
    </div>
  )
}

export default function PLChart({ bets }) {
  const { isPro } = useTier()
  const data = useMemo(() => buildData(bets), [bets])

  if (data.length < 2) return null

  const finalPL    = data[data.length - 1].cumPL
  const isPositive = finalPL >= 0
  const hasCLV     = data.some(d => d.avgCLV != null)

  const clvMin = hasCLV ? Math.min(...data.map(d => d.avgCLV ?? 0)) - 1 : -6
  const clvMax = hasCLV ? Math.max(...data.map(d => d.avgCLV ?? 0)) + 1 : 4

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 pt-3 pb-2">

      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-semibold text-[var(--text-h)]">P&amp;L Trend</span>
          <span className="text-[10px] text-[var(--text)] opacity-50">cumulative profit over time</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`font-bold font-mono text-sm ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
            {finalPL >= 0 ? '+' : ''}{fmtK(finalPL)}
          </span>
          {isPro && (
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded border border-violet-500/60 text-violet-400">
              PRO
            </span>
          )}
        </div>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data} margin={{ top: 8, right: isPro && hasCLV ? 40 : 12, bottom: 0, left: 0 }}>
          <XAxis
            dataKey="label"
            tick={{ fontSize: 9, fill: 'var(--text)', opacity: 0.5 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />

          {/* Left y-axis: absolute P&L */}
          <YAxis
            yAxisId="pl"
            orientation="left"
            tickFormatter={fmtK}
            tick={{ fontSize: 9, fill: 'var(--text)', opacity: 0.5 }}
            tickLine={false}
            axisLine={false}
            width={52}
          />

          {/* Right y-axis: CLV % (only when there's data) */}
          {isPro && hasCLV && (
            <YAxis
              yAxisId="clv"
              orientation="right"
              domain={[clvMin, clvMax]}
              tickFormatter={v => `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`}
              tick={{ fontSize: 9, fill: '#a78bfa', opacity: 0.6 }}
              tickLine={false}
              axisLine={false}
              width={36}
            />
          )}

          <ReferenceLine yAxisId="pl" y={0} stroke="var(--border)" strokeDasharray="3 3" />

          <Tooltip content={<ChartTooltip />} cursor={{ stroke: 'var(--border)', strokeWidth: 1 }} />

          {/* Daily bars — coloured per-bar via Cell */}
          <Bar
            yAxisId="pl"
            dataKey="dailyPL"
            name="Daily P&L"
            maxBarSize={24}
            radius={[3, 3, 0, 0]}
            isAnimationActive={data.length < 120}
          >
            {data.map((d, i) => (
              <Cell
                key={i}
                fill={d.dailyPL >= 0 ? 'rgba(74,222,128,0.75)' : 'rgba(248,113,113,0.75)'}
              />
            ))}
          </Bar>

          {/* Cumulative line */}
          <Line
            yAxisId="pl"
            type="monotone"
            dataKey="cumPL"
            name="Cumulative"
            stroke="#4ade80"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: '#4ade80', stroke: 'var(--bg)', strokeWidth: 2 }}
            isAnimationActive={data.length < 120}
          />

          {/* CLV dashed line (pro only) */}
          {isPro && hasCLV && (
            <Line
              yAxisId="clv"
              type="monotone"
              dataKey="avgCLV"
              name="Avg CLV"
              stroke="#a78bfa"
              strokeWidth={1.5}
              strokeDasharray="5 3"
              dot={false}
              activeDot={{ r: 3, fill: '#a78bfa', stroke: 'var(--bg)', strokeWidth: 2 }}
              connectNulls
              isAnimationActive={data.length < 120}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>

      <CustomLegend />

      <div className="mt-1 flex justify-end">
        <span className="text-[9px] text-[var(--text)] opacity-30 select-none">
          {data.length} days
        </span>
      </div>
    </div>
  )
}
