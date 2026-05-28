import { useState } from 'react'
import { FlaskConical, X, ChevronRight } from 'lucide-react'

const THRESHOLD = 50

/**
 * SampleSizeWarning
 *
 * Shown whenever the user has fewer than 50 settled bets.
 * The self-learning pipeline (loss analysis, strategy agent, confidence
 * calibration, performance weights) needs real history to produce meaningful
 * adjustments. Below 50 bets these features run but their outputs are noisy.
 *
 * Props:
 *   settledBets  — number of Won/Lost bets in the tracker (required)
 *   onNavigate   — optional fn called when user clicks "Go to Tracker →"
 *                  (pass null to suppress that link)
 *   compact      — render a slimmer single-line variant (for TrackerPage header)
 */
export default function SampleSizeWarning({ settledBets = 0, onNavigate = null, compact = false }) {
  const [dismissed, setDismissed] = useState(false)

  if (dismissed || settledBets >= THRESHOLD) return null

  const remaining = THRESHOLD - settledBets
  const pct       = Math.round((settledBets / THRESHOLD) * 100)

  // ── Compact variant ────────────────────────────────────────────────────────
  if (compact) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-amber-500/25 bg-amber-500/8 px-3 py-2">
        <FlaskConical size={13} className="text-amber-400 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-xs text-amber-400 font-medium leading-snug">
            Self-learning needs more data —{' '}
            <span className="font-bold">{settledBets}/{THRESHOLD}</span> settled bets
            ({remaining} to go)
          </p>
          <div className="mt-1 h-1 rounded-full bg-amber-500/15 overflow-hidden">
            <div
              className="h-full rounded-full bg-amber-400/60 transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
        <button
          onClick={() => setDismissed(true)}
          className="text-amber-400/60 hover:text-amber-400 transition-colors shrink-0"
          title="Dismiss"
        >
          <X size={13} />
        </button>
      </div>
    )
  }

  // ── Full variant ───────────────────────────────────────────────────────────
  return (
    <div className="rounded-xl border border-amber-500/25 bg-amber-500/8 px-5 py-4">
      <div className="flex items-start gap-3">

        {/* Icon */}
        <div className="w-8 h-8 rounded-lg border border-amber-500/30 bg-amber-500/12 flex items-center justify-center shrink-0 mt-0.5">
          <FlaskConical size={15} className="text-amber-400" />
        </div>

        {/* Body */}
        <div className="flex-1 min-w-0 space-y-2.5">
          <div>
            <p className="text-sm font-semibold text-amber-400 leading-snug">
              Self-learning system is warming up
            </p>
            <p className="text-xs text-[var(--text)] opacity-80 mt-0.5 leading-relaxed">
              The AI advisory, model calibration, and auto-adjustment pipeline all improve
              with settled bet history. You have{' '}
              <span className="font-semibold text-[var(--text-h)]">{settledBets}</span> settled
              {settledBets === 1 ? ' bet' : ' bets'} — reliability increases significantly
              once you reach{' '}
              <span className="font-semibold text-[var(--text-h)]">{THRESHOLD}</span>.
            </p>
          </div>

          {/* Progress bar */}
          <div>
            <div className="flex justify-between items-center mb-1">
              <span className="text-[10px] text-amber-400/80 font-medium uppercase tracking-wide">
                Progress to reliable insights
              </span>
              <span className="text-[10px] text-amber-400 font-bold tabular-nums">
                {settledBets}/{THRESHOLD}
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-amber-500/15 overflow-hidden">
              <div
                className="h-full rounded-full bg-amber-400/70 transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
            <p className="text-[10px] text-[var(--text)] opacity-55 mt-1">
              {remaining} more {remaining === 1 ? 'bet' : 'bets'} to go
            </p>
          </div>

          {/* What's affected */}
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {[
              'AI Advisory quality',
              'Confidence calibration',
              'Market weight learning',
              'Threshold auto-tuning',
            ].map(feature => (
              <span key={feature} className="inline-flex items-center gap-1 text-[10px] text-amber-400/70">
                <span className="inline-block w-1 h-1 rounded-full bg-amber-400/50 shrink-0" />
                {feature}
              </span>
            ))}
          </div>

          {/* CTA */}
          {onNavigate && (
            <button
              onClick={onNavigate}
              className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-400 hover:text-amber-300 transition-colors"
            >
              Track picks in the Bet Tracker <ChevronRight size={12} />
            </button>
          )}
        </div>

        {/* Dismiss */}
        <button
          onClick={() => setDismissed(true)}
          className="text-amber-400/40 hover:text-amber-400/80 transition-colors shrink-0"
          title="Dismiss for this session"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
