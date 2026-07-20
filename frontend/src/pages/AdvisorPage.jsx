import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import AIAdvisorPanel from '../components/signals/AIAdvisorPanel'
import AIChatPanel from '../components/signals/AIChatPanel'
import UpgradePrompt from '../components/shared/UpgradePrompt'
import useTier from '../hooks/useTier'

export default function AdvisorPage({ onUpgrade }) {
  const { isPro } = useTier()

  const today = (() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
  })()

  if (!isPro) {
    return (
      <UpgradePrompt
        required="pro"
        feature="The AI Advisory Council analyses each day's signals and delivers structured verdicts — Strong, Mixed, or Caution — for each market. Ask the AI chat lets you query any pick or fixture in plain English. Upgrade to Pro to unlock."
        onUpgrade={onUpgrade}
      />
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Sparkles size={18} className="text-[var(--accent)]" />
        <h1 className="text-lg font-semibold text-[var(--text-h)]">AI Advisory</h1>
      </div>

      <AIAdvisorPanel date={today} tabMode />

      {/* Chat section */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-px bg-[var(--border)]" />
        <span className="text-[10px] font-bold text-[var(--text)] opacity-40 tracking-widest uppercase">Ask the AI</span>
        <div className="flex-1 h-px bg-[var(--border)]" />
      </div>

      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5">
        <AIChatPanel />
      </div>
    </div>
  )
}
