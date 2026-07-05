import { useState } from 'react'
import {
  Sparkles, TrendingUp, TrendingDown, Target, AlertTriangle,
  Zap, Shield, Crosshair, Brain, ChevronDown, ChevronUp,
  Activity, GitMerge, MapPin, ArrowDownCircle, ExternalLink,
} from 'lucide-react'

// ── Type styles ───────────────────────────────────────────────────────────────

const TYPE_STYLE = {
  success: { border: 'border-emerald-500/40', bg: 'bg-emerald-500/8',  icon: 'text-emerald-400' },
  warning: { border: 'border-amber-500/40',   bg: 'bg-amber-500/8',    icon: 'text-amber-400'   },
  danger:  { border: 'border-red-500/40',     bg: 'bg-red-500/8',      icon: 'text-red-400'     },
  info:    { border: 'border-blue-500/40',    bg: 'bg-blue-500/8',     icon: 'text-blue-400'    },
  neutral: { border: 'border-[var(--border)]', bg: 'bg-[var(--code-bg)]', icon: 'text-[var(--text)]' },
}

// ── Max drawdown helper ───────────────────────────────────────────────────────

function maxDrawdown(trend) {
  if (!trend?.length) return { drawdown: 0, current: 0 }
  let peak = -Infinity
  let maxDD = 0
  let lastCumulative = 0
  for (const d of trend) {
    const c = d.cumulative ?? 0
    if (c > peak) peak = c
    const dd = peak - c
    if (dd > maxDD) maxDD = dd
    lastCumulative = c
  }
  const lastPeak = trend.reduce((p, d) => Math.max(p, d.cumulative ?? 0), -Infinity)
  const currentDD = lastPeak - lastCumulative
  return { drawdown: maxDD, current: Math.max(currentDD, 0) }
}

// ── Pure-JS insight computation ───────────────────────────────────────────────

function computeInsights(data) {
  const insights = []
  const s = data.summary
  if (!s || (s.settled_bets ?? 0) < 5) return insights

  const byMarket     = (data.byMarket     ?? []).filter(m => (m.bets  ?? 0) >= 5)
  const byConfidence = (data.byConfidence ?? []).filter(c => (c.bets  ?? 0) >= 5)
  const byAgreement  = (data.byAgreement  ?? []).filter(a => (a.bets  ?? 0) >= 5)
  const byLeague     = (data.byLeague     ?? []).filter(l => (l.bets  ?? 0) >= 8 && l.league !== 'Unknown')
  const trend        = data.trend ?? []

  // 1 — Overall ROI
  if ((s.settled_bets ?? 0) >= 10) {
    const roi = s.roi ?? 0
    insights.push({
      type: roi >= 5 ? 'success' : roi >= 0 ? 'info' : 'danger',
      Icon: roi >= 0 ? TrendingUp : TrendingDown,
      text: `Overall ROI is ${roi >= 0 ? '+' : ''}${roi}% across ${s.settled_bets} settled bets`,
      sub: roi >= 10 ? 'Strong edge — stay consistent with your staking plan.'
         : roi >= 0  ? 'In profitable territory. Keep sample size growing to confirm the edge.'
         :             'Currently underwater. Focus stakes on high-confidence signals until you find your footing.',
    })
  }

  // 2 — Best market
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

  // 3 — Worst market
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

  // 4 — Engine agreement type signal
  if (byAgreement.length >= 2) {
    const both    = byAgreement.find(a => a.agreement === 'Both')
    const singles = byAgreement.filter(a => a.agreement !== 'Both' && a.agreement !== 'Contradiction')
    if (both && singles.length > 0) {
      const avgSingleRoi = singles.reduce((s, a) => s + a.roi, 0) / singles.length
      const gap = both.roi - avgSingleRoi
      if (Math.abs(gap) > 8) {
        insights.push({
          type: gap > 0 ? 'success' : 'warning',
          Icon: GitMerge,
          text: gap > 0
            ? `"Both engines agree" is your most reliable filter`
            : `Single-engine signals are currently outperforming`,
          sub: gap > 0
            ? `Both: ${both.roi >= 0 ? '+' : ''}${both.roi}% ROI vs single-engine avg: ${avgSingleRoi.toFixed(1)}% ROI. Apply the Both agreement filter on the Signals page to focus your bankroll.`
            : `Both engines: ${both.roi}% ROI vs single-engine avg: ${avgSingleRoi.toFixed(1)}% ROI. Unusual — check if sample size is sufficient before adjusting strategy.`,
        })
      }
    }
  }

  // 5 — Best league
  if (byLeague.length > 0) {
    const bestLeague = [...byLeague].sort((a, b) => b.roi - a.roi)[0]
    const worstLeague = [...byLeague].sort((a, b) => a.roi - b.roi)[0]
    if (bestLeague.roi > 5) {
      insights.push({
        type: 'success',
        Icon: MapPin,
        text: `${bestLeague.league} is your top-performing competition`,
        sub: `${bestLeague.win_rate}% win rate · ${bestLeague.roi > 0 ? '+' : ''}${bestLeague.roi}% ROI over ${bestLeague.bets} bets. When tomorrow's signals include this league, weight your stake accordingly.`,
        leagueFilter: bestLeague.league,
      })
    } else if (worstLeague.roi < -15 && worstLeague !== bestLeague) {
      insights.push({
        type: 'warning',
        Icon: MapPin,
        text: `Avoid ${worstLeague.league} — consistent losses`,
        sub: `${worstLeague.roi}% ROI over ${worstLeague.bets} bets. Skip signals from this competition until the system suppresses it or ROI recovers.`,
      })
    }
  }

  // 6 — Confidence sweet spot
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

  // 7 — Current streak
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

  // 8 — Max drawdown
  if (trend.length >= 5) {
    const { drawdown, current } = maxDrawdown(trend)
    if (drawdown > 0) {
      const severity = current / drawdown
      insights.push({
        type: severity > 0.7 ? 'danger' : severity > 0.3 ? 'warning' : 'neutral',
        Icon: ArrowDownCircle,
        text: current > 0.5
          ? `Current drawdown: −${current.toFixed(1)} units (max was −${drawdown.toFixed(1)})`
          : `Drawdown fully recovered — max was −${drawdown.toFixed(1)} units`,
        sub: current > 0.5
          ? severity > 0.7
            ? 'Near period low. Do not increase stake sizes until you recover ground — protect the bankroll first.'
            : 'In a moderate dip. Normal variance — stay with your staking plan and review loss patterns in Strategy tab.'
          : 'Bankroll is at or near its period high. Good position to stay disciplined and not overbet.',
      })
    }
  }

  // 9 — CLV signal
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

  // 10 — 7-day trend direction
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
          ? `Last 7 days: ${recent7 >= 0 ? '+' : ''}${recent7.toFixed(1)} units vs prior week: ${prev7 >= 0 ? '+' : ''}${prev7.toFixed(1)} units.`
          : `Last 7 days: ${recent7.toFixed(1)} units vs prior week: ${prev7 >= 0 ? '+' : ''}${prev7.toFixed(1)} units. Review recent losses in the Strategy tab.`,
      })
    }
  }

  return insights
}

// ── Insight card ──────────────────────────────────────────────────────────────

function InsightCard({ insight, onApplySignalFilter }) {
  const { type, Icon, text, sub, leagueFilter } = insight
  const style = TYPE_STYLE[type] ?? TYPE_STYLE.neutral
  return (
    <div className={`flex gap-3 rounded-xl border px-4 py-3 ${style.border} ${style.bg}`}>
      <div className={`mt-0.5 shrink-0 ${style.icon}`}>
        <Icon size={15} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-[var(--text-h)] leading-snug">{text}</p>
        {sub && (
          <p className="text-xs text-[var(--text)] opacity-75 leading-relaxed mt-0.5">{sub}</p>
        )}
        {leagueFilter && onApplySignalFilter && (
          <button
            onClick={() => onApplySignalFilter({ league: leagueFilter })}
            className="mt-2 inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-1 rounded-md border border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/15 transition-colors"
          >
            <ExternalLink size={9} />
            Filter Signals to this league
          </button>
        )}
      </div>
    </div>
  )
}

// ── Pick source widget ────────────────────────────────────────────────────────

function PickSourceWidget({ bySource }) {
  const rows = (bySource ?? [])
    .filter(r => (r.bets ?? 0) >= 5)
    .sort((a, b) => b.roi - a.roi)
    .slice(0, 5)

  if (rows.length === 0) return null

  return (
    <div className="border-t border-[var(--border)] px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-[var(--text)] opacity-50 mb-2">
        Pick source performance
      </p>
      <div className="space-y-1.5">
        {rows.map(r => {
          const roiColor = r.roi >= 5 ? 'text-emerald-400' : r.roi >= 0 ? 'text-[var(--text-h)]' : 'text-red-400'
          const barW = Math.min(Math.abs(r.roi) / 30 * 100, 100)
          const barColor = r.roi >= 0 ? 'bg-emerald-500/50' : 'bg-red-500/50'
          return (
            <div key={r.source} className="flex items-center gap-2">
              <span className="text-xs text-[var(--text)] opacity-80 w-28 shrink-0 truncate">{r.source}</span>
              <div className="flex-1 h-1.5 bg-[var(--code-bg)] rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${barColor}`} style={{ width: `${barW}%` }} />
              </div>
              <span className={`text-xs font-mono font-semibold w-14 text-right shrink-0 ${roiColor}`}>
                {r.roi >= 0 ? '+' : ''}{r.roi}% ROI
              </span>
              <span className="text-[10px] text-[var(--text)] opacity-50 w-12 text-right shrink-0">
                {r.win_rate}% wr
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function BriefingPanel({ data, onApplySignalFilter }) {
  const [open, setOpen] = useState(true)

  if (!data) return null

  const insights = computeInsights(data)
  const hasSource = (data.bySource ?? []).filter(r => (r.bets ?? 0) >= 5).length > 0

  if (insights.length === 0 && !hasSource) return null

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
          {insights.length > 0 && (
            <span className="text-xs font-semibold px-2 py-0.5 rounded-md bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
              {insights.length} insights
            </span>
          )}
        </div>
        {open
          ? <ChevronUp size={14} className="text-[var(--text)] opacity-50" />
          : <ChevronDown size={14} className="text-[var(--text)] opacity-50" />
        }
      </button>

      {open && (
        <>
          {/* Insights grid */}
          {insights.length > 0 && (
            <div className="p-4 grid gap-2.5 sm:grid-cols-2">
              {insights.map((ins, i) => (
                <InsightCard key={i} insight={ins} onApplySignalFilter={onApplySignalFilter} />
              ))}
            </div>
          )}

          {/* Pick source widget */}
          <PickSourceWidget bySource={data.bySource} />
        </>
      )}
    </div>
  )
}
