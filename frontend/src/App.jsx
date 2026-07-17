import { useState, useEffect } from 'react'
import AppShell from './components/layout/AppShell'
import SignalsPage from './pages/SignalsPage'
import DeepDivePage from './pages/DeepDivePage'
import TrackerPage from './pages/TrackerPage'
import AnalyticsPage from './pages/AnalyticsPage'
import ToolsPage from './pages/ToolsPage'
import PricingPage from './pages/PricingPage'
import AdminPage from './pages/AdminPage'
import AccountPage from './pages/AccountPage'
import LandingPage from './pages/LandingPage'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import PaymentCallbackPage from './pages/PaymentCallbackPage'
import { useSettings } from './store/useSettings'
import { useAuth } from './context/AuthContext'

export default function App() {
  const [activePage, setActivePage] = useState('signals')
  const [deepDiveFixtureId, setDeepDiveFixtureId] = useState(null)
  const [authMode, setAuthMode] = useState('login')
  const [pendingSignalFilter, setPendingSignalFilter] = useState(null)
  const { settings, update } = useSettings()
  const { user, loading } = useAuth()

  // Handle Paystack callback redirect (/payment/callback?reference=...)
  const isPaymentCallback = window.location.pathname === '/payment/callback'

  useEffect(() => {
    // After payment callback resolves, reload user and go to pricing
    if (isPaymentCallback && user) {
      setActivePage('pricing')
    }
  }, [isPaymentCallback, user])

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

  // Handle /reset-password?token= URL
  const resetToken = !user && window.location.pathname === '/reset-password'
    ? new URLSearchParams(window.location.search).get('token')
    : null

  if (!user) {
    if (resetToken) return <ResetPasswordPage token={resetToken} onDone={() => { window.history.replaceState({}, '', '/'); setAuthMode('landing') }} />
    if (authMode === 'forgot') return <ForgotPasswordPage onBack={() => setAuthMode('login')} />
    if (authMode === 'register') return <RegisterPage onSwitch={() => setAuthMode('login')} onBack={() => setAuthMode('login')} />
    if (authMode === 'login') return <LoginPage onSwitch={() => setAuthMode('register')} onForgot={() => setAuthMode('forgot')} onBack={null} />
    return <LandingPage onSignIn={() => setAuthMode('login')} onSignUp={() => setAuthMode('register')} />
  }

  if (isPaymentCallback) {
    return (
      <PaymentCallbackPage onDone={() => {
        window.history.replaceState({}, '', '/')
        setActivePage('pricing')
        // Force a page reload to refresh user context from /auth/me
        window.location.href = '/'
      }} />
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
