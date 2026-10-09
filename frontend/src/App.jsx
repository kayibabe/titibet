import { useState, useEffect } from 'react'
import AppShell from './components/layout/AppShell'
import SignalsPage from './pages/SignalsPage'
import DeepDivePage from './pages/DeepDivePage'
import TrackerPage from './pages/TrackerPage'
import AnalyticsPage from './pages/AnalyticsPage'
import AdvisorPage from './pages/AdvisorPage'
import ToolsPage from './pages/ToolsPage'
import PricingPage from './pages/PricingPage'
import AdminPage from './pages/AdminPage'
import AccountPage from './pages/AccountPage'
import { useSettings } from './store/useSettings'
import { useAuth } from './context/AuthContext'

export default function App() {
  const [activePage, setActivePage] = useState('signals')
  const [deepDiveFixtureId, setDeepDiveFixtureId] = useState(null)
  const [pendingSignalFilter, setPendingSignalFilter] = useState(null)
  const { settings, update } = useSettings()
  const { user, loading } = useAuth()

  useEffect(() => {
    function handler(e) { setActivePage(e.detail) }
    window.addEventListener('titibet:navigate', handler)
    return () => window.removeEventListener('titibet:navigate', handler)
  }, [])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg-page)]">
        <div className="text-[var(--text)] opacity-80 text-sm animate-pulse">Loading…</div>
      </div>
    )
  }

  function handleDeepDive(fixtureId) {
    setDeepDiveFixtureId(fixtureId)
    setActivePage('deepdive')
  }

  function handleBackFromDeepDive() {
    setActivePage('signals')
    setDeepDiveFixtureId(null)
  }

  const goToPricing = () => setActivePage('pricing')

  function handleApplySignalFilter(filter) {
    setPendingSignalFilter(filter)
    setActivePage('signals')
  }

  function renderPage() {
    switch (activePage) {
      case 'signals':
        return <SignalsPage
          settings={settings}
          onDeepDive={handleDeepDive}
          onUpgrade={goToPricing}
          onNavigateToTracker={() => setActivePage('tracker')}
          initialFilter={pendingSignalFilter}
          onFilterConsumed={() => setPendingSignalFilter(null)}
        />
      case 'deepdive':
        return <DeepDivePage fixtureId={deepDiveFixtureId} settings={settings} onBack={handleBackFromDeepDive} />
      case 'tracker':
        return <TrackerPage user={user} settings={settings} onUpgrade={goToPricing} />
      case 'analytics':
        return <AnalyticsPage onUpgrade={goToPricing} onApplySignalFilter={handleApplySignalFilter} onNavigate={setActivePage} settings={settings} />
      case 'advisor':
        return <AdvisorPage onUpgrade={goToPricing} />
      case 'tools':
        return <ToolsPage settings={settings} onUpgrade={goToPricing} onUpdate={update} />
      case 'account':
        return <AccountPage onUpgrade={goToPricing} />
      case 'pricing':
        return <PricingPage />
      case 'admin':
        return user?.is_admin ? <AdminPage /> : null
      default:
        return null
    }
  }

  return (
    <AppShell activePage={activePage} onNavigate={setActivePage}>
      {renderPage()}
    </AppShell>
  )
}
