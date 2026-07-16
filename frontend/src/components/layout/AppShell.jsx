import { useState, useEffect, useRef } from 'react'
import { LayoutDashboard, Menu, X, User, LogOut, Settings } from 'lucide-react'
import Sidebar from './Sidebar'
import BottomNav from './BottomNav'
import { useAuth } from '../../context/AuthContext'

function LiveClock() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const date = now.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  const time = now.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true })

  return (
    <div className="hidden sm:flex flex-col items-end leading-none gap-0.5">
      <span className="font-mono text-sm font-semibold text-[var(--text-h)]">{time}</span>
      <span className="text-[10px] text-[var(--text)] opacity-80 tracking-wide">{date}</span>
    </div>
  )
}

const PAGE_TITLES = {
  signals:   'Signals',
  deepdive:  'Deep Dive',
  tracker:   'Bet Tracker',
  analytics: 'Analytics',
  tools:     'Tools',
  admin:     'User Panel',
  account:   'My Account',
  pricing:   'Plans & Pricing',
}

function UserMenu({ onNavigate }) {
  const { user, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const initial = (user?.name || user?.email || '?')[0].toUpperCase()

  return (
    <div ref={ref} className="relative lg:hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-8 h-8 rounded-full bg-[var(--accent)] flex items-center justify-center text-white text-sm font-bold shrink-0"
        aria-label="Account menu"
      >
        {initial}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-2 w-44 bg-[var(--bg)] border border-[var(--border)] rounded-xl shadow-xl py-1 z-50">
          {user?.email && (
            <p className="px-3 py-2 text-[10px] text-[var(--text)] opacity-60 truncate border-b border-[var(--border)]">
              {user.email}
            </p>
          )}
          <button
            onClick={() => { onNavigate('account'); setOpen(false) }}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] transition-colors"
          >
            <Settings size={14} /> Account
          </button>
          <button
            onClick={() => { logout(); setOpen(false) }}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-400 hover:text-red-300 hover:bg-[var(--code-bg)] transition-colors"
          >
            <LogOut size={14} /> Sign out
          </button>
        </div>
      )}
    </div>
  )
}

export default function AppShell({ activePage, onNavigate, children }) {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const pageTitle = PAGE_TITLES[activePage] ?? ''

  function navigate(page) {
    onNavigate(page)
    setDrawerOpen(false)
  }

  return (
    <div className="h-svh flex flex-col bg-[var(--bg)] overflow-hidden">

      {/* ── Full-width sticky top header ── */}
      <header className="sticky top-0 z-50 h-14 shrink-0 flex items-center gap-3 px-4 lg:px-6 bg-[var(--bg)] border-b border-[var(--border)]">

        {/* Mobile hamburger */}
        <button
          onClick={() => setDrawerOpen(v => !v)}
          aria-label={drawerOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={drawerOpen}
          aria-controls="mobile-sidebar"
          className="lg:hidden p-1.5 rounded-lg text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] transition-colors"
        >
          {drawerOpen ? <X size={20} /> : <Menu size={20} />}
        </button>

        {/* Brand logo — always visible */}
        <div className="flex items-center gap-1">
          <div className="shrink-0 w-9 h-9">
            <img
              src="/falcon.png"
              alt="TiTiBet"
              className="w-full h-full object-contain"
            />
          </div>
          <div className="leading-none">
            <span className="text-base font-bold text-[var(--text-h)] tracking-tight">TiTiBet</span>
            <span className="block text-[10px] text-[var(--accent)] font-semibold tracking-widest uppercase mt-0.5 opacity-80">
              Intelligence Platform
            </span>
          </div>
        </div>

        {/* Pillars — centred, desktop only */}
        <span className="hidden lg:flex flex-1 justify-center text-xs font-semibold text-[var(--accent)] tracking-widest uppercase select-none pointer-events-none">
          Signals&nbsp;·&nbsp;Tracker&nbsp;·&nbsp;Analytics
        </span>

        {/* Right side: user avatar (mobile) + clock (sm+) */}
        <div className="ml-auto flex items-center gap-2">
          <UserMenu onNavigate={navigate} />
          <LiveClock />
        </div>
      </header>

      {/* ── Below header: sidebar + content ── */}
      <div className="flex flex-1 min-h-0">

        {/* Mobile drawer overlay */}
        {drawerOpen && (
          <div
            className="lg:hidden fixed inset-0 z-40 bg-black/60"
            style={{ top: '3.5rem' }}
            onClick={() => setDrawerOpen(false)}
          />
        )}

        {/* Mobile slide-in drawer — starts below the top header */}
        <div
          id="mobile-sidebar"
          className={`lg:hidden fixed left-0 z-50 transition-transform duration-200 ${
            drawerOpen ? 'translate-x-0' : '-translate-x-full'
          }`}
          style={{ top: '3.5rem', height: 'calc(100% - 3.5rem)' }}
        >
          <Sidebar activePage={activePage} onNavigate={navigate} />
        </div>

        {/* Desktop sidebar */}
        <div className="hidden lg:block shrink-0">
          <Sidebar activePage={activePage} onNavigate={navigate} />
        </div>

        {/* Main content */}
        <div className="flex-1 flex flex-col min-w-0 overflow-y-auto">
          <main className="flex-1 px-4 py-5 lg:px-6 lg:py-6 w-full max-w-5xl mx-auto space-y-5 pb-24 lg:pb-6">
            {pageTitle && (
              <>
                {/* Visible heading on desktop */}
                <h1 aria-hidden="true" className="hidden lg:block text-base font-bold text-[var(--text-h)] tracking-tight pb-4 border-b border-[var(--border)] mb-2">
                  {pageTitle}
                </h1>
                {/* Screen-reader-only heading on mobile so landmark is always present */}
                <h1 className="sr-only">{pageTitle}</h1>
              </>
            )}
            {children}
          </main>
        </div>
      </div>

      {/* ── Mobile bottom nav ── */}
      <BottomNav activePage={activePage} onNavigate={navigate} />
    </div>
  )
}
