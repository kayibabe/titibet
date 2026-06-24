import { useMemo, useRef, useEffect } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { fmtK } from '../../utils/format'

const round2 = v => Math.round(v * 100) / 100
const DOT_SPACING = 32   // px per data point when scrolling

function buildData(bets) {
  const settled = bets
    .filter(b => b.result_status !== 'Pending' && b.profit_loss != null && b.event_date)
    .sort((a, b) => (a.event_date < b.event_date ? -1 : a.event_date > b.event_date ? 1 : (a.id ?? 0) - (b.id ?? 0)))

  let running = 0
  return settled.map((b, i) => {
    running += b.profit_loss
    const d = new Date(`${b.event_date}T00:00:00`)
    return {
      idx: i + 1,
      label: d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }),
      cumPL: round2(running),
      periodPL: round2(b.profit_loss),
      result: b.result_status,
      match: b.match_name || b.league || '',
      market: b.market_type || '',
      odds: b.odds,
    }
  })
}

function CustomDot({ cx, cy, payload }) {
  if (cx == null || cy == null) return null
  const won = payload.result === 'Won'
  return (
    <circle
      cx={cx} cy={cy} r={5}
      fill={won ? '#4ade80' : '#f87171'}
      stroke={won ? '#166534' : '#7f1d1d'}
      strokeWidth={1}
    />
  )
}

function CustomActiveDot({ cx, cy, payload }) {
  if (cx == null || cy == null) return null
  const won = payload.result === 'Won'
  return (
    <circle
      cx={cx} cy={cy} r={7}
      fill={won ? '#4ade80' : '#f87171'}
      stroke="var(--bg)"
      strokeWidth={2}
    />
  )
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const p = payload[0]?.payload
  if (!p) return null
  const pos = p.periodPL >= 0
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 shadow-lg text-xs space-y-0.5 max-w-[180px]">
      <p className="font-semibold text-[var(--text-h)] truncate">{p.match || label}</p>
      {p.market && <p className="text-[var(--text)] opacity-60">{p.market}</p>}
      <p className="text-[var(--text)] opacity-60">Odds: {p.odds ?? '—'}</p>
      <p>
        Result:{' '}
        <span className={`font-semibold ${pos ? 'text-green-400' : 'text-red-400'}`}>
          {p.result} ({p.periodPL >= 0 ? '+' : ''}{fmtK(p.periodPL)})
        </span>
      </p>
      <p>
        Cumulative:{' '}
        <span className={`font-bold font-mono ${p.cumPL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {p.cumPL >= 0 ? '+' : ''}{fmtK(p.cumPL)}
        </span>
      </p>
    </div>
  )
}

const CHART_HEIGHT = 200
const Y_AXIS_WIDTH  = 56
const MARGIN        = { top: 8, right: 16, bottom: 0, left: 0 }

export default function PLChart({ bets }) {
  const scrollRef = useRef(null)
  const data = useMemo(() => buildData(bets), [bets])

  // Scroll to the rightmost (most recent) end on mount and whenever data changes
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollLeft = scrollRef.current.scrollWidth
    }
  }, [data])

  if (data.length < 2) return null

  const finalPL    = data[data.length - 1].cumPL
  const isPositive = finalPL >= 0
  const lineColor  = isPositive ? '#4ade80' : '#f87171'
  const gradId     = isPositive ? 'plGradientGreen' : 'plGradientRed'

  // Chart canvas is wide enough so every dot has DOT_SPACING px of room
  const chartWidth = Math.max(data.length * DOT_SPACING, 600)

  // Show a label roughly every 7 dots (dense enough to orient, sparse enough to read)
  const labelEvery = Math.max(1, Math.floor(data.length / Math.floor(chartWidth / 60)))

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 pt-3 pb-2">

      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <span className="text-xs font-semibold text-[var(--text-h)]">Cumulative P&amp;L</span>
          <span className={`ml-2 text-sm font-bold font-mono ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
            {finalPL >= 0 ? '+' : ''}{fmtK(finalPL)}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px] text-[var(--text)] opacity-60">
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-green-400" /> Won
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-red-400" /> Lost
          </span>
          <span className="opacity-40 italic hidden sm:inline">← scroll →</span>
        </div>
      </div>

      {/* Y-axis (pinned, does not scroll) + scrollable chart body */}
      <div className="flex">

        {/* Pinned Y-axis */}
        <div style={{ width: Y_AXIS_WIDTH, flexShrink: 0, height: CHART_HEIGHT }}>
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <AreaChart data={data} margin={MARGIN}>
              <YAxis
                tickFormatter={fmtK}
                tick={{ fontSize: 9, fill: 'var(--text)', opacity: 0.5 }}
                tickLine={false}
                axisLine={false}
                width={Y_AXIS_WIDTH}
              />
              {/* Invisible area just to render the Y-axis at the right scale */}
              <Area type="monotone" dataKey="cumPL" stroke="none" fill="none" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Scrollable chart body */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-x-auto"
          style={{
            scrollbarWidth: 'thin',
            scrollbarColor: 'var(--border) transparent',
          }}
        >
          <div style={{ width: chartWidth, height: CHART_HEIGHT }}>
            <AreaChart
              width={chartWidth}
              height={CHART_HEIGHT}
              data={data}
              margin={{ top: MARGIN.top, right: MARGIN.right, bottom: MARGIN.bottom, left: 0 }}
            >
              <defs>
                <linearGradient id="plGradientGreen" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#4ade80" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#4ade80" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="plGradientRed" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#f87171" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#f87171" stopOpacity={0.02} />
                </linearGradient>
              </defs>

              <XAxis
                dataKey="label"
                tick={{ fontSize: 9, fill: 'var(--text)', opacity: 0.5 }}
                tickLine={false}
                axisLine={false}
                interval={labelEvery - 1}
              />
              <YAxis hide />
              <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" />
              <Tooltip
                content={<ChartTooltip />}
                cursor={{ stroke: 'var(--border)', strokeWidth: 1 }}
              />
              <Area
                type="monotone"
                dataKey="cumPL"
                stroke={lineColor}
                strokeWidth={2.5}
                fill={`url(#${gradId})`}
                dot={<CustomDot />}
                activeDot={<CustomActiveDot />}
                isAnimationActive={data.length < 200}
              />
            </AreaChart>
          </div>
        </div>

      </div>

      {/* Scroll hint bar */}
      <div className="mt-1 flex justify-end">
        <span className="text-[9px] text-[var(--text)] opacity-30 select-none">
          {data.length} bets · scroll to explore
        </span>
      </div>
    </div>
  )
}
