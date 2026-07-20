import { Lock, Zap, Shield } from 'lucide-react'

/**
 * UpgradePrompt — subscription gate component.
 *
 * Variants:
 *   "page"   — full-page centred card (default). Use for whole-page locks.
 *   "inline" — compact banner row. Use inside a panel/section.
 *   "blur"   — renders children blurred with lock overlay. Use for partial blurs.
 *
 * Props:
 *   required     — 'pro'
 *   feature      — short description shown in the gate copy
 *   onUpgrade    — callback for the "View Plans" button
 *   children     — rendered content (for variant="blur" only)
 */

const STYLE = {
  pro: {
    color:  'text-blue-400',
    border: 'border-blue-500/30',
    bg:     'bg-blue-500/8',
    btn:    'bg-blue-600 hover:bg-blue-500',
    icon:   Zap,
    label:  'Pro',
  },
}

export default function UpgradePrompt({
  required = 'pro',
  feature  = null,
  variant  = 'page',
  onUpgrade,
  children,
}) {
  const c    = STYLE[required] || STYLE.pro
  const Icon = c.icon

  // ── blur variant — renders children behind a blurred overlay ─────────────
  if (variant === 'blur') {
    return (
      <div className="relative overflow-hidden rounded-xl">
        {/* Blurred content */}
        <div className="pointer-events-none select-none blur-[3px] brightness-50 saturate-50">
          {children}
        </div>
        {/* Lock overlay */}
        <div className={`absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center rounded-xl border ${c.border} ${c.bg}`}>
          <div className={`w-10 h-10 rounded-full border ${c.border} flex items-center justify-center`}>
            <Lock size={17} className={c.color} />
          </div>
          <div>
            <p className={`text-sm font-bold ${c.color}`}>{c.label} Feature</p>
            {feature && (
              <p className="text-xs text-[var(--text)] opacity-75 mt-0.5">
                {feature}
              </p>
            )}
          </div>
          {onUpgrade && (
            <button
              onClick={onUpgrade}
              className={`px-4 py-1.5 rounded-lg text-white text-xs font-semibold transition-colors ${c.btn}`}
            >
              Upgrade to {c.label}
            </button>
          )}
        </div>
      </div>
    )
  }

  // ── inline variant — compact single-row banner ────────────────────────────
  if (variant === 'inline') {
    return (
      <div className={`flex items-center gap-3 rounded-lg border ${c.border} ${c.bg} px-4 py-3`}>
        <div className={`w-7 h-7 rounded-full border ${c.border} flex items-center justify-center shrink-0`}>
          <Lock size={13} className={c.color} />
        </div>
        <div className="flex-1 min-w-0">
          <span className={`text-xs font-semibold ${c.color}`}>{c.label} Feature</span>
          {feature && (
            <span className="text-xs text-[var(--text)] opacity-75 ml-1.5">{feature}</span>
          )}
        </div>
        {onUpgrade && (
          <button
            onClick={onUpgrade}
            className={`shrink-0 px-3 py-1.5 rounded-lg text-white text-xs font-semibold transition-colors ${c.btn}`}
          >
            Upgrade
          </button>
        )}
      </div>
    )
  }

  // ── page variant — full centred card (default) ────────────────────────────
  return (
    <div className={`rounded-xl border ${c.border} ${c.bg} p-10 flex flex-col items-center gap-4 text-center`}>
      <div className={`w-14 h-14 rounded-full border ${c.border} flex items-center justify-center`}>
        <Lock size={22} className={c.color} />
      </div>
      <div>
        <div className="flex items-center justify-center gap-1.5 mb-1">
          <Icon size={14} className={c.color} />
          <p className={`text-base font-bold ${c.color}`}>{c.label} Feature</p>
        </div>
        {feature ? (
          <p className="text-sm text-[var(--text)] opacity-75 mt-1 max-w-xs">{feature}</p>
        ) : (
          <p className="text-sm text-[var(--text)] opacity-75 mt-1">
            Upgrade to <span className={`font-semibold ${c.color}`}>{c.label}</span> to unlock this feature.
          </p>
        )}
      </div>
      {onUpgrade && (
        <button
          onClick={onUpgrade}
          className={`px-6 py-2.5 rounded-lg text-white text-sm font-semibold transition-colors ${c.btn}`}
        >
          View Plans
        </button>
      )}
    </div>
  )
}
