/**
 * StreakBadge — shows current streak, longest win run, and longest loss run.
 *
 * Backend sends:
 *   current_streak_type: "Won" | "Lost" | null
 *   current_streak_len:  number
 *   longest_win_streak:  number
 *   longest_loss_streak: number
 */
export default function StreakBadge({ streaks }) {
  if (!streaks) return null

  const {
    current_streak_type,
    current_streak_len = 0,
    longest_win_streak = 0,
    longest_loss_streak = 0,
  } = streaks

  const isWin  = current_streak_type === 'Won'
  const isLoss = current_streak_type === 'Lost'
  const hasStreak = current_streak_len > 0

  const currentColor = isWin  ? 'text-green-400'
                     : isLoss ? 'text-red-400'
                     : 'text-[var(--text)]'
  const currentBg   = isWin  ? 'bg-green-500/10 border-green-500/25'
                     : isLoss ? 'bg-red-500/10 border-red-500/25'
                     : 'bg-[var(--code-bg)] border-[var(--border)]'

  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* Current streak pill */}
      <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm ${currentBg}`}>
        <span className={`font-bold text-base tabular-nums ${currentColor}`}>
          {hasStreak ? current_streak_len : 0}
        </span>
        <span className={`text-xs font-medium ${currentColor}`}>
          {isWin  ? 'win streak' :
           isLoss ? 'loss streak' :
           'no active streak'}
        </span>
      </div>

      {/* Divider */}
      <span className="text-[var(--border)] text-lg hidden sm:inline">·</span>

      {/* Best / Worst record */}
      <div className="flex items-center gap-4 text-xs text-[var(--text)]">
        <div className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-green-400 inline-block" />
          <span className="opacity-75">Longest win run</span>
          <span className="font-bold text-green-400 tabular-nums">{longest_win_streak}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-red-400 inline-block" />
          <span className="opacity-75">Longest loss run</span>
          <span className="font-bold text-red-400 tabular-nums">{longest_loss_streak}</span>
        </div>
      </div>
    </div>
  )
}
