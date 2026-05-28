import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { fmtK } from '../../utils/format'

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const point = payload[0]?.payload
  const val   = payload[0]?.value ?? 0
  const start = point?._start ?? 100
  const pct   = ((val / start) - 1) * 100
  const pctStr = `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
  const valColor = val >= start ? '#4ade80' : '#f87171'

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2.5 text-xs shadow-lg min-w-[160px]">
      {point?.date && (
        <p className="text-[var(--text)] opacity-70 mb-1.5">{point.date}</p>
      )}
      {point?.market && (
        <p className="text-[var(--text)] opacity-80 mb-1 truncate max-w-[180px]">{point.market}</p>
      )}
      <div className="flex justify-between gap-4">
        <span className="text-[var(--text)] opacity-75">Bankroll</span>
        <span className="font-mono font-semibold" style={{ color: valColor }}>
          {fmtK(val)}
        </span>
      </div>
      <div className="flex justify-between gap-4 mt-0.5">
        <span className="text-[var(--text)] opacity-75">Return</span>
        <span className="font-mono font-semibold" style={{ color: valColor }}>
          {pctStr}
        </span>
      </div>
      {point?.won != null && (
        <div className="mt-1.5 pt-1.5 border-t border-[var(--border)]">
          <span className={point.won ? 'text-green-400' : 'text-red-400'}>
            {point.won ? 'Won' : 'Lost'}
          </span>
        </div>
      )}
    </div>
  )
}

export default function BankrollChart({ curve = [] }) {
  if (!curve.length) {
    return (
      <div className="flex h-52 items-center justify-center text-sm text-[var(--text)] opacity-70">
        Run a backtest to see the bankroll curve
      </div>
    )
  }

  const startVal = curve[0]?.bankroll ?? (typeof curve[0] === 'number' ? curve[0] : 100)

  const data = curve.map((entry, i) => {
    if (typeof entry === 'number') {
      return { bet: i + 1, bankroll: entry, _start: startVal }
    }
    return {
      bet:      i + 1,
      bankroll: entry?.bankroll ?? startVal,
      date:     entry?.date ?? null,
      market:   entry?.market ?? null,
      won:      entry?.won ?? null,
      _start:   startVal,
    }
  })

  const finalVal = data[data.length - 1]?.bankroll ?? startVal
  const pct      = ((finalVal / startVal) - 1) * 100
  const isProfit = finalVal >= startVal
  const color    = isProfit ? '#4ade80' : '#f87171'

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs">
        <span className="text-[var(--text)] opacity-65">
          Base{' '}
          <span className="font-mono font-semibold text-[var(--text-h)]">{fmtK(startVal)}</span>
          <span className="opacity-70 ml-1">(K10 flat stake)</span>
        </span>
        <span className="font-mono font-semibold" style={{ color }}>
          {fmtK(finalVal)}{' '}
          <span className="opacity-80">
            ({pct >= 0 ? '+' : ''}{pct.toFixed(1)}%)
          </span>
        </span>
      </div>

      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="bankrollGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={color} stopOpacity={0.25} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="bet"
            tick={{ fontSize: 10, fill: 'var(--text)', opacity: 0.6 }}
            axisLine={false}
            tickLine={false}
            label={{ value: 'bet #', position: 'insideBottomRight', offset: -4, fontSize: 10, fill: 'var(--text)', opacity: 0.4 }}
          />
          <YAxis
            tick={{ fontSize: 10, fill: 'var(--text)', opacity: 0.6 }}
            axisLine={false}
            tickLine={false}
            width={58}
            tickFormatter={v => fmtK(v, 0)}
          />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine y={startVal} stroke="var(--border)" strokeDasharray="3 3" />
          <Area
            type="monotone"
            dataKey="bankroll"
            stroke={color}
            strokeWidth={2}
            fill="url(#bankrollGrad)"
            dot={false}
            activeDot={{ r: 3, fill: color, strokeWidth: 0 }}
          />
        </AreaChart>
      </ResponsiveContainer>

      <p className="text-[10px] text-[var(--text)] opacity-65 text-center">
        Simulated with K10 flat stake per bet · each point = one signal that passed the engine
      </p>
    </div>
  )
}
