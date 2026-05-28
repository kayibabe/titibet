import { TrendingUp, TrendingDown, Target, BarChart2, Layers, Clock } from 'lucide-react'
import { fmtPLCompact, fmtKCompact } from '../../utils/format'

function KPICard({ icon: Icon, label, value, fullValue, sub, tone = 'neutral', size = 'normal' }) {
  const tones = {
    positive: {
      icon:  'text-green-400',
      value: 'text-green-400',
      bg:    'bg-green-500/5 border-green-500/20',
    },
    negative: {
      icon:  'text-red-400',
      value: 'text-red-400',
      bg:    'bg-red-500/5 border-red-500/20',
    },
    accent: {
      icon:  'text-[var(--accent)]',
      value: 'text-[var(--accent)]',
      bg:    'bg-[var(--accent-bg,rgba(99,102,241,0.05))] border-[var(--accent-border,rgba(99,102,241,0.2))]',
    },
    neutral: {
      icon:  'text-[var(--text)]',
      value: 'text-[var(--text-h)]',
      bg:    'bg-[var(--bg)] border-[var(--border)]',
    },
    muted: {
      icon:  'text-[var(--text)] opacity-80',
      value: 'text-[var(--text-h)]',
      bg:    'bg-[var(--code-bg)] border-[var(--border)]',
    },
  }
  const t = tones[tone] ?? tones.neutral
  const valueSize = size === 'large' ? 'text-2xl' : 'text-xl'

  return (
    <div className={`rounded-xl border px-3 py-3 flex flex-col gap-1 min-w-0 ${t.bg}`}>
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] font-semibold text-[var(--text)] opacity-80 uppercase tracking-wide leading-tight">
          {label}
        </span>
        {Icon && <Icon size={12} className={`shrink-0 ${t.icon}`} />}
      </div>
      <span
        className={`font-bold tabular-nums leading-tight ${valueSize} ${t.value}`}
        title={fullValue ?? undefined}
      >
        {value ?? '—'}
      </span>
      {sub && (
        <span className="text-[10px] text-[var(--text)] opacity-65 mt-0.5 leading-snug">
          {sub}
        </span>
      )}
    </div>
  )
}

export default function KPIRow({ summary }) {
  if (!summary) return null

  const {
    roi = 0,
    win_rate = 0,
    total_profit_loss = 0,
    avg_odds,
    wins = 0,
    losses = 0,
    settled_bets = 0,
    pending_bets = 0,
    total_stake = 0,
    avg_clv = null,
    clv_coverage_pct = 0,
    positive_clv_pct = null,
  } = summary

  const roiTone  = roi >= 20 ? 'positive' : roi >= 0 ? 'neutral' : 'negative'
  const wrTone   = win_rate >= 60 ? 'positive' : win_rate >= 45 ? 'neutral' : 'negative'
  const plTone   = total_profit_loss > 0 ? 'positive' : total_profit_loss < 0 ? 'negative' : 'neutral'
  const clvTone  = avg_clv == null ? 'muted' : avg_clv > 0 ? 'positive' : avg_clv < 0 ? 'negative' : 'neutral'
  const RoiIcon  = roi >= 0 ? TrendingUp : TrendingDown
  const ClvIcon  = avg_clv == null || avg_clv >= 0 ? TrendingUp : TrendingDown

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-7 gap-2">
      <KPICard
        icon={RoiIcon}
        label="ROI"
        value={`${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%`}
        sub="return on investment"
        tone={roiTone}
      />
      <KPICard
        icon={Target}
        label="Hit Rate"
        value={`${win_rate.toFixed(1)}%`}
        sub={`${wins}W · ${losses}L`}
        tone={wrTone}
      />
      <KPICard
        icon={BarChart2}
        label="Net P&L"
        value={fmtPLCompact(total_profit_loss)}
        fullValue={`${total_profit_loss >= 0 ? '+' : '-'}K${Math.abs(total_profit_loss).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
        sub={`on ${settled_bets} settled`}
        tone={plTone}
      />
      <KPICard
        icon={Layers}
        label="Avg Odds"
        value={avg_odds != null ? avg_odds.toFixed(2) : '—'}
        sub="avg odds taken"
        tone="neutral"
      />
      <KPICard
        icon={Layers}
        label="Total Staked"
        value={fmtKCompact(total_stake)}
        fullValue={`K${total_stake.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
        sub={`${settled_bets} settled`}
        tone="muted"
      />
      <KPICard
        icon={Clock}
        label="Pending"
        value={pending_bets}
        sub="awaiting results"
        tone="muted"
      />
      <KPICard
        icon={ClvIcon}
        label="Avg CLV"
        value={avg_clv != null ? `${avg_clv >= 0 ? '+' : ''}${avg_clv.toFixed(1)}%` : '—'}
        sub={
          avg_clv != null
            ? `${clv_coverage_pct.toFixed(0)}% coverage · ${positive_clv_pct?.toFixed(0) ?? '?'}% positive`
            : 'no closing data yet'
        }
        tone={clvTone}
      />
    </div>
  )
}
