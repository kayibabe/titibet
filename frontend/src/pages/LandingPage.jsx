import { useState, useEffect } from 'react'
import {
  TrendingUp, Shield, Zap, BarChart3, CheckCircle, Lock,
  ChevronRight, Clock, Trophy, Globe, Users, Activity,
  Brain, Database, Target, Star, ArrowRight, Menu, X
} from 'lucide-react'

const TICKER_ITEMS = [
  '🟢 AI Signal: Manchester City Home Win — 78% confidence',
  '✅ Settled: Arsenal BTTS Yes — WON',
  '🟢 New Signal: Barcelona Over 2.5 Goals — 82% confidence',
  '📊 Model Accuracy Updated: 76.4% (last 30 days)',
  '🟢 Banker Pick: PSG Home Win — 89% confidence',
  '✅ Settled: Bayern München Home Win — WON',
  '🟢 High Value: Liverpool vs Chelsea — Draw 24%',
  '📊 Champions League Signals Added: 8 matches',
  '🟢 AI Signal: Inter Milan Home Win — 74% confidence',
  '✅ Settled: Real Madrid Over 2.5 — WON',
]

const PREVIEW_SIGNALS = [
  {
    home: 'Manchester City', away: 'Arsenal',
    league: 'Premier League', country: 'England',
    kickoff: '20:00 Today', market: 'Home Win',
    probability: 76, odds: 1.72, bookmaker: 'Bet365',
    confidence: 'High', agreement: 'Both Models', locked: false,
  },
  {
    home: 'Real Madrid', away: 'FC Barcelona',
    league: 'La Liga', country: 'Spain',
    kickoff: '20:00 Today', market: 'Over 2.5 Goals',
    probability: 71, odds: 1.65, bookmaker: 'Betway',
    confidence: 'High', agreement: 'Both Models', locked: false,
  },
  {
    home: 'Bayern München', away: 'Borussia Dortmund',
    league: 'Bundesliga', country: 'Germany',
    kickoff: '17:30 Today', market: 'BTTS Yes',
    probability: 68, odds: 1.80, bookmaker: '1xBet',
    confidence: 'Medium', agreement: 'Both Models', locked: true,
  },
]

const LEAGUES = [
  'Premier League', 'La Liga', 'Serie A', 'Bundesliga',
  'Ligue 1', 'Champions League', 'Europa League', 'Eredivisie',
  'Primeira Liga', 'Scottish Prem', 'MLS', 'World Cup',
]

const STATS = [
  { value: '50K+', label: 'Predictions Generated', icon: Target },
  { value: '76%', label: 'Model Accuracy', icon: TrendingUp },
  { value: '10K+', label: 'Active Subscribers', icon: Users },
  { value: '100+', label: 'Leagues Covered', icon: Globe },
]

const HOW_IT_WORKS = [
  {
    icon: Database,
    title: 'Live Data Ingestion',
    desc: 'Fixtures, odds, and form data pulled from 100+ leagues multiple times daily — always fresh, never stale.',
  },
  {
    icon: Brain,
    title: 'Dual-Model Analysis',
    desc: 'Bayesian and Poisson models run independently, then fuse into a single ranked signal with agreement scoring.',
  },
  {
    icon: BarChart3,
    title: 'Ranked Signals',
    desc: 'Only the highest-confidence picks surface. Free users get 5; Pro users see every signal, ranked by edge.',
  },
]

const FREE_FEATURES = [
  '5 top signals per day',
  'Probability & odds display',
  'Dual-model agreement indicator',
  'League & market breakdown',
]

const PRO_FEATURES = [
  'All signals, fully ranked',
  'AI Acca-of-the-Day picks',
  'AI Advisory chat',
  'CLV tracking & closing line value',
  'Bet tracker with P&L analytics',
  'Kelly criterion stake sizing',
  'Model performance dashboard',
]

function probBarColor(p) {
  if (p >= 70) return 'bg-emerald-500'
  if (p >= 50) return 'bg-amber-500'
  if (p >= 35) return 'bg-orange-500'
  return 'bg-rose-500'
}

function probTextColor(p) {
  if (p >= 70) return 'text-emerald-400'
  if (p >= 50) return 'text-amber-400'
  if (p >= 35) return 'text-orange-400'
  return 'text-rose-400'
}

function confidenceBadge(conf) {
  if (conf === 'High') return 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
  if (conf === 'Medium') return 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
  return 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
}

function PreviewSignalCard({ signal, rank }) {
  const barColor = probBarColor(signal.probability)
  const textColor = probTextColor(signal.probability)

  return (
    <div className={`relative rounded-2xl border bg-white/5 backdrop-blur-sm overflow-hidden transition-all duration-300 hover:-translate-y-1 ${
      signal.probability >= 70
        ? 'border-emerald-500/40 shadow-[0_8px_24px_rgba(16,185,129,0.15)]'
        : 'border-amber-500/30'
    }`}>
      {signal.locked && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center backdrop-blur-sm bg-[#0e0f14]/60 rounded-2xl">
          <Lock className="w-6 h-6 text-purple-400 mb-2" />
          <p className="text-sm font-semibold text-white">Pro Signal</p>
          <p className="text-xs text-white/50 mt-1">Unlock with Pro plan</p>
        </div>
      )}

      <div className="p-4">
        {/* Header */}
        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="flex items-center gap-1.5 mb-1">
              <span className="text-[10px] uppercase tracking-widest text-white/40 font-medium">{signal.country} · {signal.league}</span>
              {rank === 1 && (
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400 border border-amber-500/30">#1</span>
              )}
            </div>
            <p className="text-sm font-semibold text-white/90 leading-tight">
              {signal.home} <span className="text-white/30 font-normal">vs</span> {signal.away}
            </p>
          </div>
          <div className="flex items-center gap-1 text-white/40 text-xs shrink-0 ml-2">
            <Clock className="w-3 h-3" />
            <span>{signal.kickoff}</span>
          </div>
        </div>

        {/* Market + Probability */}
        <div className="mb-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/20">
              {signal.market}
            </span>
            <span className={`text-xl font-black tabular-nums ${textColor}`}>{signal.probability}%</span>
          </div>
          <div className="h-2 rounded-full bg-white/10 overflow-hidden">
            <div
              className={`h-full rounded-full ${barColor} transition-all duration-700`}
              style={{ width: `${signal.probability}%` }}
            />
          </div>
        </div>

        {/* Footer row */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${confidenceBadge(signal.confidence)}`}>
              {signal.confidence}
            </span>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-500/15 text-purple-300 border border-purple-500/20">
              Both Models
            </span>
          </div>
          <div className="text-right">
            <span className="text-sm font-bold text-white/80">@{signal.odds}</span>
            <span className="text-[10px] text-white/30 ml-1">{signal.bookmaker}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function LandingNav({ onSignIn, onSignUp }) {
  const [scrolled, setScrolled] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const handler = () => setScrolled(window.scrollY > 20)
    window.addEventListener('scroll', handler, { passive: true })
    return () => window.removeEventListener('scroll', handler)
  }, [])

  function scrollTo(id) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
    setMobileOpen(false)
  }

  return (
    <nav className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
      scrolled ? 'bg-[#0e0f14]/95 backdrop-blur-md border-b border-white/10 shadow-[0_4px_24px_rgba(0,0,0,0.4)]' : 'bg-transparent'
    }`}>
      <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
        {/* Logo */}
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-purple-500/30 border border-purple-500/40 flex items-center justify-center">
            <TrendingUp className="w-4 h-4 text-purple-400" />
          </div>
          <span className="font-bold text-white text-base tracking-tight">TiTiBet</span>
        </div>

        {/* Desktop links */}
        <div className="hidden md:flex items-center gap-6">
          {[['how-it-works', 'How It Works'], ['signals-preview', 'Signals'], ['pricing', 'Pricing']].map(([id, label]) => (
            <button key={id} onClick={() => scrollTo(id)}
              className="text-sm text-white/60 hover:text-white transition-colors cursor-pointer">
              {label}
            </button>
          ))}
        </div>

        {/* Auth buttons */}
        <div className="flex items-center gap-2">
          <button onClick={onSignIn}
            className="hidden sm:block text-sm text-white/70 hover:text-white transition-colors px-3 py-1.5 cursor-pointer">
            Sign In
          </button>
          <button onClick={onSignUp}
            className="text-sm font-semibold px-4 py-1.5 rounded-lg bg-purple-500 hover:bg-purple-400 text-white transition-colors cursor-pointer">
            Get Free Picks
          </button>
          <button onClick={() => setMobileOpen(!mobileOpen)}
            className="md:hidden ml-1 p-1.5 text-white/60 hover:text-white transition-colors cursor-pointer">
            {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      {mobileOpen && (
        <div className="md:hidden bg-[#0e0f14]/98 border-t border-white/10 px-4 py-4 space-y-2">
          {[['how-it-works', 'How It Works'], ['signals-preview', 'Signals'], ['pricing', 'Pricing']].map(([id, label]) => (
            <button key={id} onClick={() => scrollTo(id)}
              className="block w-full text-left text-sm text-white/70 hover:text-white py-2 transition-colors cursor-pointer">
              {label}
            </button>
          ))}
          <button onClick={onSignIn}
            className="block w-full text-left text-sm text-white/70 hover:text-white py-2 transition-colors cursor-pointer">
            Sign In
          </button>
        </div>
      )}
    </nav>
  )
}

function ActivityTicker() {
  const items = [...TICKER_ITEMS, ...TICKER_ITEMS]

  return (
    <div className="bg-white/5 border-b border-white/10 overflow-hidden" style={{ marginTop: '64px' }}>
      <div className="ticker-track py-2">
        {items.map((item, i) => (
          <span key={i} className="text-xs text-white/50 whitespace-nowrap px-8">
            {item}
          </span>
        ))}
      </div>
    </div>
  )
}

export default function LandingPage({ onSignIn, onSignUp }) {
  return (
    <div className="min-h-screen bg-[#0e0f14] text-white selection:bg-purple-500/30">
      <LandingNav onSignIn={onSignIn} onSignUp={onSignUp} />
      <ActivityTicker />

      {/* Hero */}
      <section className="relative overflow-hidden pt-20 pb-24 px-4 sm:px-6">
        {/* Background glow */}
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[500px] bg-purple-600/10 rounded-full blur-3xl pointer-events-none" />
        <div className="absolute top-20 left-1/4 w-[300px] h-[300px] bg-purple-800/8 rounded-full blur-3xl pointer-events-none" />

        <div className="relative max-w-3xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-purple-500/15 border border-purple-500/30 text-purple-300 text-xs font-medium mb-6">
            <Activity className="w-3 h-3" />
            AI-Powered Football Intelligence · Updated 3× Daily
          </div>

          <h1 className="text-4xl sm:text-5xl md:text-6xl font-black text-white leading-[1.1] tracking-tight mb-5">
            Smarter Picks.{' '}
            <span className="text-purple-400">Better Returns.</span>
          </h1>

          <p className="text-base sm:text-lg text-white/55 max-w-2xl mx-auto mb-8 leading-relaxed">
            Dual-model AI analyzes 100+ leagues, surfaces only the highest-confidence signals —
            so you bet with conviction, not guesswork. Free to start.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3 mb-10">
            <button onClick={onSignUp}
              className="flex items-center gap-2 px-6 py-3 rounded-xl bg-purple-500 hover:bg-purple-400 text-white font-semibold text-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[0_8px_24px_rgba(168,85,247,0.4)] cursor-pointer">
              Get Free Picks
              <ArrowRight className="w-4 h-4" />
            </button>
            <button onClick={() => document.getElementById('how-it-works')?.scrollIntoView({ behavior: 'smooth' })}
              className="flex items-center gap-2 px-6 py-3 rounded-xl border border-white/15 hover:border-white/30 text-white/70 hover:text-white font-medium text-sm transition-all duration-200 cursor-pointer">
              See How It Works
            </button>
          </div>

          {/* Trust line */}
          <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs text-white/35">
            <span className="flex items-center gap-1.5"><CheckCircle className="w-3.5 h-3.5 text-emerald-500" /> 10,000+ subscribers</span>
            <span className="flex items-center gap-1.5"><CheckCircle className="w-3.5 h-3.5 text-emerald-500" /> 76% model accuracy</span>
            <span className="flex items-center gap-1.5"><CheckCircle className="w-3.5 h-3.5 text-emerald-500" /> No credit card required</span>
          </div>
        </div>
      </section>

      {/* Stats */}
      <section className="py-12 px-4 sm:px-6 border-y border-white/8">
        <div className="max-w-5xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-6">
          {STATS.map(({ value, label, icon: Icon }) => (
            <div key={label} className="text-center stat-animate">
              <div className="w-10 h-10 rounded-xl bg-purple-500/15 border border-purple-500/20 flex items-center justify-center mx-auto mb-3">
                <Icon className="w-5 h-5 text-purple-400" />
              </div>
              <div className="text-2xl sm:text-3xl font-black text-white mb-1">{value}</div>
              <div className="text-xs text-white/40 leading-tight">{label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Signal Preview */}
      <section id="signals-preview" className="py-20 px-4 sm:px-6">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-12">
            <p className="text-xs uppercase tracking-widest text-purple-400 font-medium mb-3">Live Signal Preview</p>
            <h2 className="text-2xl sm:text-3xl font-bold text-white mb-3">What You'll See Every Day</h2>
            <p className="text-sm text-white/45 max-w-lg mx-auto">
              Real AI signals, ranked by confidence. These are examples of today's picks — sign up to see the full ranked list.
            </p>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
            {PREVIEW_SIGNALS.map((signal, i) => (
              <PreviewSignalCard key={i} signal={signal} rank={i + 1} />
            ))}
          </div>

          <div className="text-center">
            <button onClick={onSignUp}
              className="inline-flex items-center gap-2 text-sm text-purple-400 hover:text-purple-300 font-medium transition-colors cursor-pointer group">
              + 40 more signals today — sign up free to see all
              <ChevronRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
            </button>
          </div>
        </div>
      </section>

      {/* How It Works */}
      <section id="how-it-works" className="py-20 px-4 sm:px-6 bg-white/[0.02]">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-12">
            <p className="text-xs uppercase tracking-widest text-purple-400 font-medium mb-3">The Process</p>
            <h2 className="text-2xl sm:text-3xl font-bold text-white mb-3">How It Works</h2>
            <p className="text-sm text-white/45 max-w-lg mx-auto">
              From raw match data to a ranked signal in your feed — three steps, fully automated.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-6">
            {HOW_IT_WORKS.map(({ icon: Icon, title, desc }, i) => (
              <div key={title} className="relative rounded-2xl border border-white/10 bg-white/5 p-6 hover:border-purple-500/30 hover:-translate-y-1 transition-all duration-300">
                <div className="w-10 h-10 rounded-xl bg-purple-500/20 border border-purple-500/30 flex items-center justify-center mb-4">
                  <Icon className="w-5 h-5 text-purple-400" />
                </div>
                <div className="text-[10px] font-bold text-white/25 mb-1.5 tracking-widest">STEP {i + 1}</div>
                <h3 className="text-base font-semibold text-white mb-2">{title}</h3>
                <p className="text-sm text-white/45 leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="py-20 px-4 sm:px-6">
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-12">
            <p className="text-xs uppercase tracking-widest text-purple-400 font-medium mb-3">Simple Pricing</p>
            <h2 className="text-2xl sm:text-3xl font-bold text-white mb-3">Start Free, Upgrade When Ready</h2>
            <p className="text-sm text-white/45">No lock-in. Cancel any time.</p>
          </div>

          <div className="grid md:grid-cols-2 gap-6 max-w-2xl mx-auto">
            {/* Free */}
            <div className="rounded-2xl border border-white/15 bg-white/5 p-6">
              <div className="mb-6">
                <p className="text-xs text-white/40 uppercase tracking-widest mb-1">Free</p>
                <div className="flex items-end gap-1">
                  <span className="text-4xl font-black text-white">$0</span>
                  <span className="text-sm text-white/30 mb-1.5">/month</span>
                </div>
                <p className="text-xs text-white/35 mt-1">No card required</p>
              </div>
              <ul className="space-y-3 mb-8">
                {FREE_FEATURES.map(f => (
                  <li key={f} className="flex items-start gap-2.5 text-sm text-white/60">
                    <CheckCircle className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                    {f}
                  </li>
                ))}
              </ul>
              <button onClick={onSignUp}
                className="w-full py-2.5 rounded-xl border border-white/20 hover:border-white/40 text-sm font-medium text-white/80 hover:text-white transition-all cursor-pointer">
                Get Started Free
              </button>
            </div>

            {/* Pro */}
            <div className="rounded-2xl border border-purple-500/50 bg-purple-500/10 p-6 relative overflow-hidden">
              <div className="absolute top-4 right-4">
                <span className="text-[10px] font-bold px-2.5 py-1 rounded-full bg-purple-500 text-white">MOST POPULAR</span>
              </div>
              <div className="mb-6">
                <p className="text-xs text-purple-300 uppercase tracking-widest mb-1">Pro</p>
                <div className="flex items-end gap-1">
                  <span className="text-4xl font-black text-white">$9</span>
                  <span className="text-sm text-white/40 mb-1.5">/month</span>
                </div>
                <p className="text-xs text-white/35 mt-1">Billed monthly</p>
              </div>
              <ul className="space-y-3 mb-8">
                {PRO_FEATURES.map(f => (
                  <li key={f} className="flex items-start gap-2.5 text-sm text-white/80">
                    <CheckCircle className="w-4 h-4 text-purple-400 shrink-0 mt-0.5" />
                    {f}
                  </li>
                ))}
              </ul>
              <button onClick={onSignUp}
                className="w-full py-2.5 rounded-xl bg-purple-500 hover:bg-purple-400 text-sm font-semibold text-white transition-all hover:-translate-y-0.5 hover:shadow-[0_6px_20px_rgba(168,85,247,0.4)] cursor-pointer">
                Start Pro — $9/month
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* League Coverage */}
      <section className="py-16 px-4 sm:px-6 border-t border-white/8">
        <div className="max-w-5xl mx-auto text-center">
          <p className="text-xs uppercase tracking-widest text-purple-400 font-medium mb-3">Coverage</p>
          <h2 className="text-xl sm:text-2xl font-bold text-white mb-8">100+ Leagues. Every Day.</h2>
          <div className="flex flex-wrap justify-center gap-2">
            {LEAGUES.map(league => (
              <span key={league}
                className="text-xs px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-white/50 hover:text-white/80 hover:border-white/25 transition-colors">
                {league}
              </span>
            ))}
            <span className="text-xs px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-white/30">
              + 90 more
            </span>
          </div>
        </div>
      </section>

      {/* CTA Banner */}
      <section className="py-20 px-4 sm:px-6">
        <div className="max-w-2xl mx-auto text-center">
          <div className="relative rounded-3xl border border-purple-500/30 bg-purple-500/10 p-10 overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-br from-purple-600/10 to-transparent pointer-events-none" />
            <Shield className="w-10 h-10 text-purple-400 mx-auto mb-4" />
            <h2 className="text-2xl sm:text-3xl font-bold text-white mb-3">Ready to Bet Smarter?</h2>
            <p className="text-sm text-white/50 mb-6 leading-relaxed">
              Join 10,000+ subscribers getting AI-ranked football signals every day.
              Free to start — no card required.
            </p>
            <button onClick={onSignUp}
              className="inline-flex items-center gap-2 px-8 py-3 rounded-xl bg-purple-500 hover:bg-purple-400 text-white font-semibold text-sm transition-all hover:-translate-y-0.5 hover:shadow-[0_8px_24px_rgba(168,85,247,0.5)] cursor-pointer">
              Get Free Picks Now
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-white/8 py-12 px-4 sm:px-6">
        <div className="max-w-5xl mx-auto">
          <div className="grid sm:grid-cols-3 gap-8 mb-10">
            <div>
              <div className="flex items-center gap-2 mb-3">
                <div className="w-6 h-6 rounded-md bg-purple-500/30 border border-purple-500/40 flex items-center justify-center">
                  <TrendingUp className="w-3.5 h-3.5 text-purple-400" />
                </div>
                <span className="font-bold text-white text-sm">TiTiBet</span>
              </div>
              <p className="text-xs text-white/30 leading-relaxed max-w-[200px]">
                AI-powered football signals built on data, not hype.
              </p>
            </div>

            <div>
              <p className="text-xs font-semibold text-white/50 uppercase tracking-widest mb-3">Product</p>
              <ul className="space-y-2">
                {[['signals-preview', 'Signals'], ['how-it-works', 'How It Works'], ['pricing', 'Pricing']].map(([id, label]) => (
                  <li key={id}>
                    <button onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })}
                      className="text-xs text-white/35 hover:text-white/70 transition-colors cursor-pointer">
                      {label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <p className="text-xs font-semibold text-white/50 uppercase tracking-widest mb-3">Account</p>
              <ul className="space-y-2">
                <li>
                  <button onClick={onSignIn}
                    className="text-xs text-white/35 hover:text-white/70 transition-colors cursor-pointer">
                    Sign In
                  </button>
                </li>
                <li>
                  <button onClick={onSignUp}
                    className="text-xs text-white/35 hover:text-white/70 transition-colors cursor-pointer">
                    Create Account
                  </button>
                </li>
              </ul>
            </div>
          </div>

          <div className="border-t border-white/8 pt-6 flex flex-col sm:flex-row items-center justify-between gap-3">
            <p className="text-[10px] text-white/20">© 2025 TiTiBet. All rights reserved.</p>
            <p className="text-[10px] text-white/20 text-center sm:text-right max-w-md">
              TiTiBet provides statistical analysis only. Not financial or betting advice. Gamble responsibly. 18+ only.
            </p>
          </div>
        </div>
      </footer>
    </div>
  )
}
