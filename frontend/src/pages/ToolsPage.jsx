import { useState } from 'react'
import { Percent, FlaskConical, Settings as SettingsIcon } from 'lucide-react'
import ArbPage from './ArbPage'
import BacktestPage from './BacktestPage'
import SettingsPage from './SettingsPage'

// Tools — a single home for low-frequency, power-user, and configuration
// surfaces. Keeps the primary nav focused on the daily-driver pages
// (Signals · Tracker · Analytics) while keeping these one tap away.
const TABS = [
  { id: 'arbitrage', label: 'Arbitrage', icon: Percent },
  { id: 'backtest',  label: 'Backtest',  icon: FlaskConical },
  { id: 'settings',  label: 'Settings',  icon: SettingsIcon },
]

export default function ToolsPage({ settings, onUpgrade, onUpdate, initialTab = 'arbitrage' }) {
  const [tab, setTab] = useState(initialTab)

  return (
    <div className="space-y-5">
      {/* Tab bar */}
      <div className="flex gap-1 border-b border-[var(--border)] overflow-x-auto">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`shrink-0 flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === id
                ? 'border-[var(--accent)] text-[var(--accent)]'
                : 'border-transparent text-[var(--text)] hover:text-[var(--text-h)]'
            }`}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      {tab === 'arbitrage' && <ArbPage />}
      {tab === 'backtest'  && <BacktestPage settings={settings} onUpgrade={onUpgrade} />}
      {tab === 'settings'  && <SettingsPage settings={settings} onUpdate={onUpdate} />}
    </div>
  )
}
