import { useState } from 'react'
import {
  Sparkles, TrendingUp, TrendingDown, Target, AlertTriangle,
  Zap, Shield, Crosshair, Brain, ChevronDown, ChevronUp, Activity,
} from 'lucide-react'

// ── Insight types ─────────────────────────────────────────────────────────────

const TYPE_STYLE = {
  success: {
    border: 'border-emerald-500/40',
    bg:     'bg-emerald-500/8',
    icon:   'text-emerald-400',
    dot:    'bg-emerald-400',
  },
  warning: {
    border: 'border-amber-500/40',
    bg:     'bg-amber-500/8',
    icon:   'text-amber-400',
    dot:    'bg-amber-400',
  },
  danger: {
    border: 'border-red-500/40',
    bg:     'bg-red-500/8',
    icon:   'text-red-400',
    dot:    'bg-red-400',
  },
  info: {
    border: 'border-blue-500/40',
    bg:     'bg-blue-500/8',
    icon:   'text-blue-400',
    dot:    'bg-blue-400',
  },
  neutral: {
    border: 'border-[var(--border)]',
    bg:     'bg-[var(--code-bg)]',
    icon:   'text-[var(--text)]',
    dot:    'bg-slate-500',
  },
}

// ── Pure-JS insight computation ───────────────────────────────────────────────

function computeInsights(data) {
  const insights = []
  const s = data.summary

  if (!s || (s.settled_bets ?? 0) < 5) return insights

  const byMarket     = (data.byMarket     ?? []).filter(m => (m.bets ?? 0) >= 5)
  const byConfidence = (data.byConfidence ?? []).filter(c => (c.bets ?? 0) >= 5)

  // 1 — Overall ROI / profitability signal
  if ((s.settled_bets ?? 0) >= 10) {
    const roi = s.roi ?? 0
    insights.push({
      type: roi >= 5 ? 'success' : roi >= 0 ? 'info' : 'danger',
      Icon: roi >= 0 ? TrendingUp : TrendingDown,
      text: `Overall ROI is ${roi >= 0 ? '+' : ''}${roi}% across ${s.settled_bets} settled bets`,
      sub:
        roi >= 10 ? 'Strong edge — stay consistent with your staking plan.'
        : roi >= 0 ? 'In profitable territory. Keep sample size growing to confirm the edge.'
        : 'Currently underwater. Focus stakes on high-confidence signals until you find your footing.',
    })
  }

  // 2 — Best performing market
  const qualified = byMarket.filter(m => (m.settled ?? m.bets) > 0)
  if (qualified.length > 0) {
    const best = [...qualified].sort((a, b) => b.roi - a.roi)[0]
    if (best.roi > 0) {
      insights.push({
        type: 'success',
        Icon: Target,
        text: `${best.market} is your strongest market`,
        sub: `${best.win_rate}% win rate · ${best.roi > 0 ? '+' : ''}${best.roi}% ROI over ${best.bets} bet${best.bets !== 1 ? 's' : ''}. Prioritise this market when signals align.`,
      })
    }
  }

  // 3 — Underperforming market to reduce exposure on
  if (qualified.length > 1) {
    const worst = [...qualified].sort((a, b) => a.roi - b.roi)[0]
    if (worst.roi < -10) {
      insights.push({
        type: 'danger',
        Icon: AlertTriangle,
        text: `${worst.market} is consistently underperforming`,
        sub: `${worst.roi}% ROI over ${worst.bets} bet${worst.bets !== 1 ? 's' : ''}. Consider reducing exposure or waiting for the self-learning pipeline to recalibrate thresholds.`,
      })
    }
  }

  // 4 — Confidence level sweet spot
  if (byConfidence.length >= 2) {
    const sorted = [...byConfidence].sort((a, b) => b.roi - a.roi)
    const best   = sorted[0]
    const worst  = sorted[sorted.length - 1]
    if (best.roi - worst.roi > 10) {
      insights.push({
        type: 'info',
        Icon: Brain,
        text: `${best.confidence} confidence signals are your edge`,
        sub: `${best.confidence}: ${best.roi >= 0 ? '+' : ''}${best.roi}% ROI vs ${worst.confidence}: ${worst.roi}% ROI. Filter the Signals page to ${best.confidence} confidence to concentrate your bankroll where the system is most reliable.`,
      })
    }
  }

  // 5 — Current streak signal
  const streakLen  = s.current_streak_len ?? 0
  const streakType = s.current_streak_type
  if (streakLen >= 3) {
    const isWin = streakType === 'Won'
    insights.push({
      type: isWin ? 'success' : 'warning',
      Icon: isWin ? Zap : Shield,
      text: `${streakLen}-${isWin ? 'win' : 'loss'} streak in progress`,
      sub: isWin
        ? `Good momentum. Avoid chasing with oversized stakes — streaks end. Your longest win run is ${s.longest_win_streak}.`
        : `Losing runs are part of value betting. Your longest ever was ${s.longest_loss_streak}. Stick to your staking plan — do not chase.`,
    })
  }

  // 6 — CLV signal (only if meaningful coverage)
  const clvCoverage = s.clv_coverage_pct ?? 0
  const avgClv      = s.avg_clv
  if (avgClv !== null && avgClv !== undefined && clvCoverage >= 30) {
    const positive = avgClv > 0
    insights.push({
      type: positive ? 'success' : 'warning',
      Icon: Crosshair,
      text: `Closing Line Value: ${avgClv >= 0 ? '+' : ''}${avgClv}% average`,
      sub: positive
        ? `${s.positive_clv_pct}% of your bets beat closing odds — the strongest indicator of genuine long-run edge.`
        : `Fewer than half your bets beat the market before close. Try placing bets earlier when signals are posted.`,
    })
  }

  // 7 — Trend direction (last 7 days vs overall)
  const trend = data.trend ?? []
  if (trend.length >= 14) {
    const recent7 = trend.slice(-7).reduce((sum, d) => sum + (d.profit_loss ?? 0), 0)
    const prev7   = trend.slice(-14, -7).reduce((sum, d) => sum + (d.profit_loss ?? 0), 0)
    if (Math.abs(recent7 - prev7) > 0.5) {
      const improving = recent7 > prev7
      insights.push({
        type: improving ? 'success' : 'warning',
        Icon: Activity,
        text: improving ? 'Form is trending upward this week' : 'Form has dipped over the last 7 days',
        sub: improving
          ? `Last 7 days: ${recent7 >= 0 ? '+' : ''}${recent7.toFixed(0)} units vs prior week: ${prev7 >= 0 ? '+' : ''}${prev7.toFixed(0)} units.`
          : `Last 7 days: ${recent7.toFixed(0)} units vs prior week: ${prev7 >= 0 ? '+' : ''}${prev7.toFixed(0)} units. Review recent losses in the Strategy tab.`,
      })
    }
  }

  return insights
}

// ── Insight card ──────────────────────────────────────────────────────────────

function InsightCard({ insight }) {
  const { type, Icon, text, sub } = insight
  const style = TYPE_STYLE[type] ?? TYPE_STYLE.neutral
  return (
    <div className={`flex gap-3 rounded-xl border px-4 py-3 ${style.border} ${style.bg}`}>
      <div className={`mt-0.5 shrink-0 ${style.icon}`}>
        <Icon size={15} />
      </div>
      <div className="min-w-0">
        <p className="text-sm font-semibold text-[var(--text-h)] leading-snug">{text}</p>
        {sub && (
          <p className="text-xs text-[var(--text)] opacity-75 leading-relaxed mt-0.5">{sub}</p>
        )}
      </div>
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function BriefingPanel({ data }) {
  const [open, setOpen] = useState(true)

  if (!data) return null

  const insights = computeInsights(data)

  if (insights.length === 0) return null

  return (
    <div className="rounded-xl border border-[var(--border)] overflow-hidden">

      {/* Header */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-indigo-600/20 via-blue-600/15 to-[var(--code-bg)] hover:from-indigo-600/25 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <Sparkles size={14} className="text-indigo-400" />
          <span className="text-sm font-semibold text-[var(--text-h)]">Intelligence Briefing</span>
          <span className="text-xs font-semibold px-2 py-0.5 rounded-md bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
            {insights.length} insights
          </span>
        </div>
        {open
          ? <ChevronUp size={14} className="text-[var(--text)] opacity-50" />
          : <ChevronDown size={14} className="text-[var(--text)] opacity-50" />
        }
      </button>

      {/* Insights grid */}
      {open && (
        <div className="p-4 grid gap-2.5 sm:grid-cols-2">
          {insights.map((ins, i) => (
            <InsightCard key={i} insight={ins} />
          ))}
        </div>
      )}
    </div>
  )
}
