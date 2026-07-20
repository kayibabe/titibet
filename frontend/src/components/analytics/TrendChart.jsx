import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, Cell, CartesianGrid,
} from 'recharts'

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null

  const daily     = payload.find(p => p.dataKey === 'daily_pl')?.value ?? 0
  const cumul     = payload.find(p => p.dataKey === 'cumulative')?.value ?? 0
  const clv       = payload.find(p => p.dataKey === 'avg_clv')?.value
  const wins      = payload.find(p => p.dataKey === 'wins')?.payload?.wins ?? 0
  const bets      = payload.find(p => p.dataKey === 'bets')?.payload?.bets ?? 0
  const dailyColor = daily >= 0 ? '#4ade80' : '#f87171'
  const cumulColor = cumul >= 0 ? '#4ade80' : '#f87171'
  const clvColor   = clv == null ? '#94a3b8' : clv >= 0 ? '#a78bfa' : '#f87171'

  return (
    <div className="bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2.5 text-xs shadow-xl min-w-[150px]">
      <p className="text-[var(--text-h)] font-semibold mb-2">{label}</p>
      <div className="space-y-1">
        <div className="flex justify-between gap-4">
          <span className="text-[var(--text)] opacity-75">Daily P&L</span>
          <span className="font-mono font-semibold" style={{ color: dailyColor }}>
            {daily >= 0 ? '+' : ''}K{Math.abs(daily).toFixed(2)}
          </span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-[var(--text)] opacity-75">Cumulative</span>
          <span className="font-mono font-semibold" style={{ color: cumulColor }}>
            {cumul >= 0 ? '+' : ''}K{Math.abs(cumul).toFixed(2)}
          </span>
        </div>
        {clv != null && (
          <div className="flex justify-between gap-4">
            <span className="text-[var(--text)] opacity-75">Avg CLV</span>
            <span className="font-mono font-semibold" style={{ color: clvColor }}>
              {clv >= 0 ? '+' : ''}{clv.toFixed(1)}%
            </span>
          </div>
        )}
        {bets > 0 && (
          <div className="flex justify-between gap-4 pt-1 border-t border-[var(--border)]">
            <span className="text-[var(--text)] opacity-75">Bets / Wins</span>
            <span className="text-[var(--text-h)]">{bets} / {wins}</span>
          </div>
        )}
      </div>
    </div>
  )
}

export default function TrendChart({ data = [] }) {
  if (!data.length) {
    return (
      <div className="h-52 flex items-center justify-center text-[var(--text)] opacity-70 text-sm">
        No settled bets yet
      </div>
    )
  }

  // Use the cumulative that the backend already computed; map daily P&L for bars.
  const chartData = data.map(d => ({
    date: d.date ? d.date.slice(5) : '',   // "MM-DD"
    full_date: d.date,
    cumulative: d.cumulative ?? 0,
    daily_pl: d.profit_loss ?? 0,
    wins: d.wins ?? 0,
    bets: d.bets ?? 0,
    avg_clv: d.avg_clv ?? null,   // null when no closing data for that day
  }))

  const finalCumul  = chartData.at(-1)?.cumulative ?? 0
  const hasClvData  = chartData.some(d => d.avg_clv != null)

  return (
    <div className="space-y-1">
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" strokeOpacity={0.4} vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: 'var(--text)', opacity: 0.7 }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            yAxisId="cumul"
            tick={{ fontSize: 10, fill: 'var(--text)', opacity: 0.7 }}
            axisLine={false}
            tickLine={false}
            width={52}
            tickFormatter={v => `K${v >= 0 ? '' : '-'}${Math.abs(v).toFixed(0)}`}
          />
          {hasClvData && (
            <YAxis
              yAxisId="clv"
              orientation="right"
              tick={{ fontSize: 10, fill: '#a78bfa', opacity: 0.8 }}
              axisLine={false}
              tickLine={false}
              width={36}
              tickFormatter={v => `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`}
            />
          )}
          <Tooltip content={<CustomTooltip />} cursor={{ fill: 'var(--border)', fillOpacity: 0.15 }} />
          <ReferenceLine yAxisId="cumul" y={0} stroke="var(--border)" strokeDasharray="4 2" strokeOpacity={0.8} />

          {/* Daily P&L bars — green positive, red negative */}
          <Bar yAxisId="cumul" dataKey="daily_pl" name="daily_pl" maxBarSize={16} radius={[2, 2, 0, 0]}>
            {chartData.map((entry, i) => (
              <Cell key={i} fill={entry.daily_pl >= 0 ? '#4ade80' : '#f87171'} fillOpacity={0.55} />
            ))}
          </Bar>

          {/* Cumulative P&L line */}
          <Line
            yAxisId="cumul"
            type="monotone"
            dataKey="cumulative"
            name="cumulative"
            stroke={finalCumul >= 0 ? '#4ade80' : '#f87171'}
            strokeWidth={2.5}
            dot={false}
            activeDot={{ r: 4, strokeWidth: 0 }}
          />

          {/* CLV trend line — only rendered when closing data exists */}
          {hasClvData && (
            <Line
              yAxisId="clv"
              type="monotone"
              dataKey="avg_clv"
              name="avg_clv"
              stroke="#a78bfa"
              strokeWidth={1.5}
              strokeDasharray="4 2"
              dot={false}
              activeDot={{ r: 3, strokeWidth: 0 }}
              connectNulls={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="flex items-center gap-5 justify-end pr-1 text-[10px] text-[var(--text)] opacity-70">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-green-400/55" />
          Daily P&L
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-5" style={{ background: finalCumul >= 0 ? '#4ade80' : '#f87171' }} />
          Cumulative
        </span>
        {hasClvData && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-5 border-t-2 border-dashed border-violet-400" />
            Avg CLV
          </span>
        )}
      </div>
    </div>
  )
}
