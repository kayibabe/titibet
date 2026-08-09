import { useMemo } from 'react'
import {
  ComposedChart, Bar, Cell, Line, XAxis, YAxis,
  Tooltip, ReferenceLine, ResponsiveContainer,
} from 'recharts'
import { useTracker } from '../../store/useTracker'
import { fmtK } from '../../utils/format'

const GRADE_META = {
  A: { label: 'A', bar: '#639922', badge: 'bg-[#eaf3de] text-[#3b6d11]' },
  B: { label: 'B', bar: '#2a78d6', badge: 'bg-[#e6f1fb] text-[#185fa5]' },
  C: { label: 'C', bar: '#eda100', badge: 'bg-[#faeeda] text-[#854f0b]' },
  D: { label: 'D', bar: '#888780', badge: 'bg-[#F1EFE8] text-[#5F5E5A]' },
}

function buildGradeStats(bets, marketFilter) {
  const settled = bets.filter(
    b => b.result_status !== 'Pending'
      && b.profit_loss != null
      && b.signal_grade
      && (!marketFilter || b.market_type === marketFilter)
  )
  const map = {}
  for (const b of settled) {
    const g = b.signal_grade
    if (!map[g]) map[g] = { grade: g, bets: 0, wins: 0, pl: 0 }
    map[g].bets += 1
    if (b.result_status === 'Won') map[g].wins += 1
    map[g].pl += b.profit_loss
  }
  return ['A', 'B', 'C', 'D']
    .filter(g => map[g])
    .map(g => ({ ...map[g], wr: map[g].bets ? (map[g].wins / map[g].bets) * 100 : 0 }))
}

function buildDailyData(trend) {
  if (!trend?.length) return []
  let cumul = 0
  return trend.map(row => {
    cumul += row.profit_loss ?? 0
    const d = new Date(`${row.date}T00:00:00`)
    return {
      label: d.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit' }).replace('/', '-'),
      dailyPL: Math.round(row.profit_loss ?? 0),
      cumPL: Math.round(cumul),
    }
  })
}

function KPITile({ label, value, tone }) {
  const color = tone === 'pos' ? 'text-green-400' : tone === 'neg' ? 'text-red-400' : 'text-[var(--text-h)]'
  return (
    <div className="bg-[var(--code-bg)] rounded-xl p-3 space-y-1">
      <p className="text-[11px] text-[var(--text)] opacity-60">{label}</p>
      <p className={`text-xl font-bold tabular-nums ${color}`}>{value}</p>
    </div>
  )
}

function MarketCard({ row }) {
  if (!row) return null
  const wr   = row.win_rate ?? (row.wins && row.bets ? (row.wins / row.bets) * 100 : null)
  const pl   = row.net_pl ?? row.profit_loss ?? 0
  const wl   = `${row.wins ?? 0} / ${row.losses ?? 0}`
  return (
    <div className="bg-[var(--code-bg)] rounded-xl p-3 space-y-1.5">
      <p className="text-xs font-semibold text-[var(--text-h)]">{row.market}</p>
      <div className="text-[11px] space-y-0.5">
        <div className="flex justify-between"><span className="text-[var(--text)] opacity-60">Bets</span><span className="font-medium text-[var(--text-h)]">{row.bets ?? 0}</span></div>
        <div className="flex justify-between"><span className="text-[var(--text)] opacity-60">W/L</span><span className="font-medium text-[var(--text-h)]">{wl}</span></div>
        <div className="flex justify-between"><span className="text-[var(--text)] opacity-60">Win rate</span><span className="font-medium text-[var(--text-h)]">{wr != null ? `${wr.toFixed(1)}%` : '—'}</span></div>
        <div className="flex justify-between"><span className="text-[var(--text)] opacity-60">Avg odds</span><span className="font-medium text-[var(--text-h)]">{row.avg_odds?.toFixed(2) ?? '—'}</span></div>
        <div className="flex justify-between"><span className="text-[var(--text)] opacity-60">Net P/L</span><span className={`font-semibold ${pl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{pl >= 0 ? '+' : ''}{fmtK(pl)}</span></div>
      </div>
    </div>
  )
}

function GradePanel({ title, rows }) {
  const maxBets = Math.max(...rows.map(r => r.bets), 1)
  return (
    <div className="bg-[var(--code-bg)] rounded-xl p-3">
      <p className="text-[10px] font-semibold text-[var(--text)] opacity-50 uppercase tracking-wide mb-2">{title}</p>
      {rows.length === 0
        ? <p className="text-xs text-[var(--text)] opacity-40 py-2 text-center">No data</p>
        : rows.map(r => {
            const meta = GRADE_META[r.grade] || GRADE_META.D
            const barPct = (r.bets / maxBets) * 100
            return (
              <div key={r.grade} className="flex items-center gap-2 mb-2 text-[11px]">
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${meta.badge}`}>{r.grade}</span>
                <span className="text-[var(--text)] opacity-60 w-14 shrink-0">{r.bets} bets</span>
                <div className="flex-1 bg-[var(--border)] rounded-full h-1.5 overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${barPct}%`, background: meta.bar }} />
                </div>
                <span className="text-[var(--text-h)] w-10 text-right tabular-nums">{r.wr.toFixed(1)}%</span>
                <span className={`w-14 text-right tabular-nums font-semibold ${r.pl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {r.pl >= 0 ? '+' : ''}{fmtK(r.pl)}
                </span>
              </div>
            )
          })
      }
    </div>
  )
}

function DailyChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const daily = payload.find(p => p.dataKey === 'dailyPL')?.value
  const cumul  = payload.find(p => p.dataKey === 'cumPL')?.value
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-xs shadow-lg space-y-0.5">
      <p className="font-semibold text-[var(--text-h)]">{label}</p>
      {daily != null && <p className="text-[var(--text)]">Daily: <span className={`font-semibold font-mono ${daily >= 0 ? 'text-green-400' : 'text-red-400'}`}>{daily >= 0 ? '+' : ''}{fmtK(daily)}</span></p>}
      {cumul != null && <p className="text-[var(--text)]">Cumulative: <span className={`font-semibold font-mono ${cumul >= 0 ? 'text-green-400' : 'text-red-400'}`}>{cumul >= 0 ? '+' : ''}{fmtK(cumul)}</span></p>}
    </div>
  )
}

export default function SystemPerformanceDashboard({ summary, byMarket = [], trend = [] }) {
  const { bets } = useTracker()

  const systemBets = useMemo(
    () => bets.filter(b => b.source_rule_key === 'system_auto' || b.source_rule_key === 'system_dual'),
    [bets]
  )

  const gradeAll   = useMemo(() => buildGradeStats(systemBets, null),         [systemBets])
  const gradeU35   = useMemo(() => buildGradeStats(systemBets, 'Under 3.5'),  [systemBets])
  const dailyData  = useMemo(() => buildDailyData(trend), [trend])

  if (!summary) return null

  const {
    win_rate = 0, total_profit_loss = 0, roi = 0,
    wins = 0, losses = 0, settled_bets = 0,
  } = summary

  const TARGET_MARKETS = ['Under 3.5', 'Home Over 0.5', 'Over 2.5']
  const marketCards = TARGET_MARKETS.map(m => byMarket.find(r => r.market === m)).filter(Boolean)

  const isPositive = total_profit_loss >= 0
  const today = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase()

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--border)] bg-[var(--code-bg)]">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-[var(--text)] opacity-50 uppercase tracking-wide">
            System Performance · Prod · as of {today}
          </span>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* KPI row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <KPITile label="Win rate"      value={`${win_rate.toFixed(1)}%`}     tone="pos" />
          <KPITile label="Net P/L (MWK)" value={`${isPositive ? '+' : ''}${total_profit_loss.toLocaleString()}`} tone={isPositive ? 'pos' : 'neg'} />
          <KPITile label="ROI"           value={`${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%`} tone={roi >= 0 ? 'pos' : 'neg'} />
          <KPITile label="Settled bets"  value={`${wins}W / ${losses}L`}       tone="neutral" />
        </div>

        {/* By market */}
        {marketCards.length > 0 && (
          <div>
            <p className="text-[10px] font-semibold text-[var(--text)] opacity-50 uppercase tracking-wide mb-2">By market</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {marketCards.map(r => <MarketCard key={r.market} row={r} />)}
            </div>
          </div>
        )}

        {/* Grade panels */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <GradePanel title="By signal grade"         rows={gradeAll} />
          <GradePanel title="Under 3.5 · by grade"   rows={gradeU35} />
        </div>

        {/* Daily P/L chart */}
        {dailyData.length >= 2 && (
          <div>
            <p className="text-[10px] font-semibold text-[var(--text)] opacity-50 uppercase tracking-wide mb-2">
              Daily P/L (MWK)
            </p>
            <ResponsiveContainer width="100%" height={160}>
              <ComposedChart data={dailyData} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 9, fill: 'var(--text)', opacity: 0.5 }}
                  tickLine={false}
                  axisLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tickFormatter={fmtK}
                  tick={{ fontSize: 9, fill: 'var(--text)', opacity: 0.5 }}
                  tickLine={false}
                  axisLine={false}
                  width={48}
                />
                <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" />
                <Tooltip content={<DailyChartTooltip />} cursor={{ stroke: 'var(--border)', strokeWidth: 1 }} />
                <Bar dataKey="dailyPL" maxBarSize={20} radius={[3, 3, 0, 0]} isAnimationActive={false}>
                  {dailyData.map((d, i) => (
                    <Cell key={i} fill={d.dailyPL >= 0 ? 'rgba(74,222,128,0.75)' : 'rgba(248,113,113,0.75)'} />
                  ))}
                </Bar>
                <Line
                  type="monotone"
                  dataKey="cumPL"
                  stroke="#4ade80"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 3, fill: '#4ade80', stroke: 'var(--bg)', strokeWidth: 2 }}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="flex items-center gap-5 mt-1 text-[10px] text-[var(--text)] opacity-50">
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-2.5 h-2.5 rounded-sm bg-green-400 opacity-75" />Daily P&amp;L
              </span>
              <span className="flex items-center gap-1.5">
                <svg width="18" height="6" style={{ display: 'inline' }}>
                  <line x1="0" y1="3" x2="18" y2="3" stroke="#4ade80" strokeWidth="2" strokeLinecap="round" />
                </svg>
                Cumulative
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
