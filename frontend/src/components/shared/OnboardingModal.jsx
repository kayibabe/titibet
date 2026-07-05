import { useState } from 'react'
import { TrendingUp, BarChart2, CheckCircle, Send, X } from 'lucide-react'

const TG_URL = import.meta.env.VITE_TELEGRAM_FREE_URL

const BASE_STEPS = [
  {
    icon: TrendingUp,
    title: 'Welcome to TiTiBet',
    body: 'We analyse live odds using two AI models (Bayesian + Poisson) to surface the highest-value football bets.',
    iconColor: 'text-indigo-400',
    iconBg: 'bg-indigo-500/15 border-indigo-500/30',
  },
  {
    icon: BarChart2,
    title: 'Reading a Signal Card',
    body: 'Each card shows a bet outcome, win probability, and confidence level. Emerald border = high probability. Amber = medium confidence.',
    iconColor: 'text-emerald-400',
    iconBg: 'bg-emerald-500/15 border-emerald-500/30',
  },
  {
    icon: CheckCircle,
    title: 'Track Your Picks',
    body: "Click 'Track Pick' on any signal to log a bet. After the match settles, analytics unlock.",
    iconColor: 'text-blue-400',
    iconBg: 'bg-blue-500/15 border-blue-500/30',
  },
]

const TG_STEP = {
  icon: Send,
  title: 'Get Picks on Telegram',
  body: "Join TiTiBet Free on Telegram and receive today's top picks every morning — straight to your phone, before kickoff.",
  iconColor: 'text-sky-400',
  iconBg: 'bg-sky-500/15 border-sky-500/30',
  telegramUrl: TG_URL,
}

const STEPS = TG_URL ? [...BASE_STEPS, TG_STEP] : BASE_STEPS

export default function OnboardingModal({ onComplete }) {
  const [step, setStep] = useState(0)

  const current = STEPS[step]
  const Icon = current.icon
  const isLast = step === STEPS.length - 1

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
      onClick={(e) => { if (e.target === e.currentTarget) onComplete() }}
    >
      {/* Modal card */}
      <div className="relative w-full max-w-md rounded-2xl border border-white/10 bg-[var(--bg)] shadow-2xl overflow-hidden">

        {/* Top accent bar */}
        <div className="h-1 w-full bg-gradient-to-r from-indigo-500 via-blue-500 to-emerald-500" />

        {/* Dismiss button */}
        <button
          onClick={onComplete}
          className="absolute top-4 right-4 text-[var(--text)] opacity-40 hover:opacity-80 transition-opacity"
          aria-label="Skip onboarding"
        >
          <X size={16} />
        </button>

        {/* Content */}
        <div className="px-8 pt-8 pb-6 flex flex-col items-center text-center gap-5">

          {/* Icon circle */}
          <div className={`w-16 h-16 rounded-full border flex items-center justify-center ${current.iconBg}`}>
            <Icon size={28} className={current.iconColor} />
          </div>

          {/* Step label */}
          <p className="text-[10px] font-bold tracking-widest uppercase text-[var(--text)] opacity-50">
            Step {step + 1} of {STEPS.length}
          </p>

          {/* Heading */}
          <h2 className="text-xl font-bold text-[var(--text-h)] leading-snug -mt-2">
            {current.title}
          </h2>

          {/* Body */}
          <p className="text-sm text-[var(--text)] opacity-80 leading-relaxed max-w-sm">
            {current.body}
          </p>

          {/* Telegram CTA — only on the Telegram step */}
          {current.telegramUrl && (
            <a
              href={current.telegramUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-sky-500/15 border border-sky-500/30 text-sky-400 text-sm font-semibold hover:bg-sky-500/25 transition-colors -mt-1"
            >
              <Send size={14} />
              Join TiTiBet Free on Telegram
            </a>
          )}

          {/* Step dots */}
          <div className="flex items-center gap-2 mt-1">
            {STEPS.map((_, i) => (
              <button
                key={i}
                onClick={() => setStep(i)}
                aria-label={`Go to step ${i + 1}`}
                className={`rounded-full transition-all ${
                  i === step
                    ? 'w-6 h-2 bg-[var(--accent)]'
                    : 'w-2 h-2 bg-[var(--border)] hover:bg-[var(--text)]'
                }`}
              />
            ))}
          </div>

          {/* Navigation buttons */}
          <div className="flex items-center gap-3 w-full mt-1">
            {step > 0 && (
              <button
                onClick={() => setStep(s => s - 1)}
                className="flex-1 px-4 py-2.5 rounded-xl border border-[var(--border)] text-sm font-semibold text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] transition-colors"
              >
                Previous
              </button>
            )}

            {isLast ? (
              <button
                onClick={onComplete}
                className="flex-1 px-4 py-2.5 rounded-xl bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 transition-opacity"
              >
                Start Exploring →
              </button>
            ) : (
              <button
                onClick={() => setStep(s => s + 1)}
                className="flex-1 px-4 py-2.5 rounded-xl bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 transition-opacity"
              >
                Next
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
