import { useState, useEffect } from 'react'
import {
  BarChart2,
  ListChecks,
  TrendingUp,
  Wrench,
} from 'lucide-react'
import { fetchBets } from '../../api/tracker'

// Mirrors the desktop Sidebar exactly — same destinations on both devices.
const NAV_ITEMS = [
  { id: 'signals',   label: 'Signals',  icon: TrendingUp },
  { id: 'tracker',   label: 'Tracker',  icon: ListChecks },
  { id: 'analytics', label: 'Analytics',icon: BarChart2  },
  { id: 'tools',     label: 'Tools',    icon: Wrench     },
]

export default function BottomNav({ activePage, onNavigate }) {
  const [pendingCount, setPendingCount] = useState(0)

  // Load pending bet count once on mount (best-effort, silent fail)
  useEffect(() => {
    fetchBets({ result_status: 'pending' })
      .then(bets => setPendingCount(Array.isArray(bets) ? bets.length : 0))
      .catch(() => {})
  }, [])

  const badges = {
    signals: 0,
    tracker: pendingCount,
  }

  return (
    <nav
      className="lg:hidden fixed bottom-0 left-0 right-0 z-50 bg-[var(--bg)] border-t border-[var(--border)] flex items-stretch"
      style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
    >
      {NAV_ITEMS.map(({ id, label, icon: Icon }) => {
        const active = activePage === id
        const badgeCount = badges[id] ?? 0
        return (
          <button
            key={id}
            onClick={() => onNavigate(id)}
            aria-label={label}
            aria-current={active ? 'page' : undefined}
            className={`flex-1 flex flex-col items-center justify-center gap-0.5 min-h-[56px] py-2 text-[10px] transition-colors ${
              active
                ? 'text-[var(--accent)] font-semibold'
                : 'font-medium text-[var(--text)] opacity-70 hover:opacity-100'
            }`}
          >
            <div className="relative">
              <Icon size={20} className="shrink-0" />
              {badgeCount > 0 && (
                <span className="absolute -top-1 -right-1.5 min-w-[16px] h-4 rounded-full bg-[var(--accent)] text-[10px] font-bold text-white flex items-center justify-center px-1 leading-none">
                  {badgeCount > 99 ? '99+' : badgeCount}
                </span>
              )}
            </div>
            <span className="text-[10px] leading-tight">{label}</span>
          </button>
        )
      })}
    </nav>
  )
}
