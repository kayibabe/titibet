import {
  BarChart2,
  ListChecks,
  LogOut,
  Percent,
  Settings,
  ShieldCheck,
  TrendingUp,
  User,
} from 'lucide-react'
import { useAuth } from '../../context/AuthContext'

// Daily-use tool pages only — Account and Plans are accessible from the footer.
// Value Bets and Backtest live as tabs inside Signals and Analytics respectively.
const NAV_ITEMS = [
  { id: 'signals',   label: 'Signals',   icon: TrendingUp },
  { id: 'arb',       label: 'Arbitrage', icon: Percent    },
  { id: 'tracker',   label: 'Tracker',   icon: ListChecks },
  { id: 'analytics', label: 'Analytics', icon: BarChart2  },
  { id: 'settings',  label: 'Settings',  icon: Settings   },
]

const TIER_BADGE = {
  free:  { label: 'Free',  color: 'text-slate-400',  upgradeable: true  },
  pro:   { label: 'Pro',   color: 'text-blue-400',   upgradeable: false },
  elite: { label: 'Elite', color: 'text-amber-400',  upgradeable: false },
}

export default function Sidebar({ activePage, onNavigate }) {
  const { user, logout } = useAuth()
  const tier = TIER_BADGE[user?.tier] ?? TIER_BADGE.free

  return (
    <aside className="
      sticky top-14 shrink-0
      w-48 flex flex-col
      bg-[var(--bg)] border-r border-[var(--border)]
    " style={{ height: 'calc(100vh - 3.5rem)' }}>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-4 space-y-0.5">
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => {
          const active = activePage === id
          return (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              className={`
                w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
                transition-colors text-left
                ${active
                  ? 'bg-[var(--accent-bg)] text-[var(--accent)] border border-[var(--accent-border)]'
                  : 'text-[var(--text)] hover:bg-[var(--accent-bg)] hover:text-[var(--text-h)] border border-transparent'
                }
              `}
            >
              <Icon size={16} className="shrink-0" />
              {label}
            </button>
          )
        })}

        {/* Admin — elite only */}
        {user?.tier === 'elite' && (
          <>
            <div className="pt-3 pb-1 px-3">
              <span className="text-[10px] font-semibold text-[var(--text)] opacity-65 tracking-widest uppercase">Admin</span>
            </div>
            <button
              onClick={() => onNavigate('admin')}
              className={`
                w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
                transition-colors text-left
                ${activePage === 'admin'
                  ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                  : 'text-[var(--text)] hover:bg-amber-500/10 hover:text-amber-400 border border-transparent'
                }
              `}
            >
              <ShieldCheck size={16} className="shrink-0" />
              User Panel
            </button>
          </>
        )}
      </nav>

      {/* User footer — name → Account, tier → Plans, sign out */}
      <div className="px-3 py-3 border-t border-[var(--border)] space-y-1.5">
        <button
          onClick={() => onNavigate('account')}
          className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg transition-colors text-left hover:bg-[var(--code-bg)] ${
            activePage === 'account' ? 'bg-[var(--code-bg)]' : ''
          }`}
          title="Account settings"
        >
          <div className="w-7 h-7 rounded-full bg-[var(--accent-bg)] flex items-center justify-center shrink-0">
            <User size={13} className="text-[var(--accent)]" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-[var(--text-h)] truncate leading-tight">
              {user?.name || user?.email?.split('@')[0] || 'User'}
            </p>
            {/* Tier pill — clickable to Plans for free users */}
            <button
              onClick={e => { e.stopPropagation(); onNavigate('pricing') }}
              className={`text-[10px] font-semibold ${tier.color} ${
                tier.upgradeable ? 'hover:underline underline-offset-2' : 'cursor-default'
              } leading-tight`}
              title={tier.upgradeable ? 'Upgrade plan' : undefined}
            >
              {tier.label}{tier.upgradeable ? ' · Upgrade ↑' : ''}
            </button>
          </div>
        </button>

        <button
          onClick={logout}
          className="w-full flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs text-[var(--text)] opacity-80 hover:opacity-100 hover:text-red-400 hover:bg-red-500/10 transition-colors"
        >
          <LogOut size={12} />
          Sign out
        </button>
      </div>
    </aside>
  )
}
