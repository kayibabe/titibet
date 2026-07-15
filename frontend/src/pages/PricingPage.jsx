import { useState, useEffect } from 'react'
import { Check, Zap, Star } from 'lucide-react'
import { fetchPlans, initializePayment } from '../api/payments'
import { useAuth } from '../context/AuthContext'
import { fmtDate } from '../utils/format'

const FREE_FEATURES = [
  'Value signals (limited view)',
  'Basic bet tracker',
  'Market analytics',
]

function fmtMWK(amount) {
  return `K${amount.toLocaleString()}`
}

function FreeCard({ isCurrent }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5 flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-slate-500/10 border border-slate-500/20 flex items-center justify-center">
          <Star size={15} className="text-slate-400" />
        </div>
        <div>
          <p className="text-sm font-bold text-slate-300">Free</p>
          <p className="text-[10px] text-[var(--text)] opacity-80">Forever</p>
        </div>
      </div>

      <div>
        <span className="text-2xl font-bold text-[var(--text-h)]">K0</span>
        <span className="text-xs text-[var(--text)] opacity-80 ml-1">/ month</span>
      </div>

      <ul className="space-y-2 flex-1">
        {FREE_FEATURES.map((f, i) => (
          <li key={i} className="flex items-start gap-2 text-xs text-[var(--text)] opacity-75">
            <Check size={11} className="text-slate-500 shrink-0 mt-0.5" />
            {f}
          </li>
        ))}
      </ul>

      <div className={`w-full py-2 rounded-lg border text-xs font-semibold text-center ${
        isCurrent
          ? 'border-green-500/30 text-green-400'
          : 'border-[var(--border)] text-[var(--text)] opacity-50'
      }`}>
        {isCurrent ? 'Current plan' : 'Always free'}
      </div>
    </div>
  )
}

function ProCard({ plan, isCurrent, onSelect, loading }) {
  return (
    <div className="rounded-xl border border-blue-500/30 bg-blue-500/10 p-5 flex flex-col gap-4 relative">
      {plan.interval === 'yearly' && (
        <span className="absolute top-3 right-3 text-[10px] font-bold text-green-400 bg-green-500/15 border border-green-500/30 px-2 py-0.5 rounded tracking-wide">
          10% OFF
        </span>
      )}

      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-blue-500/10 border border-blue-500/30 flex items-center justify-center">
          <Zap size={15} className="text-blue-400" />
        </div>
        <div>
          <p className="text-sm font-bold text-blue-400">Pro</p>
          <p className="text-[10px] text-[var(--text)] opacity-80 capitalize">{plan.interval}</p>
        </div>
      </div>

      <div>
        <span className="text-2xl font-bold text-[var(--text-h)]">{fmtMWK(plan.price_mwk)}</span>
        <span className="text-xs text-[var(--text)] opacity-80 ml-1">
          / {plan.interval === 'yearly' ? 'year' : 'month'}
        </span>
      </div>

      <ul className="space-y-2 flex-1">
        {plan.features.map((f, i) => (
          <li key={i} className="flex items-start gap-2 text-xs text-[var(--text)]">
            <Check size={11} className="text-green-400 shrink-0 mt-0.5" />
            {f}
          </li>
        ))}
      </ul>

      {isCurrent ? (
        <div className="w-full py-2 rounded-lg border border-green-500/30 text-green-400 text-xs font-semibold text-center">
          Current plan
        </div>
      ) : (
        <button
          onClick={() => onSelect(plan.id)}
          disabled={loading === plan.id}
          className="w-full py-2 rounded-lg text-white text-xs font-semibold transition-colors bg-blue-600 hover:bg-blue-500 disabled:opacity-50"
        >
          {loading === plan.id ? 'Redirecting…' : 'Upgrade to Pro'}
        </button>
      )}
    </div>
  )
}

export default function PricingPage() {
  const { user } = useAuth()
  const [plans, setPlans] = useState([])
  const [plansLoading, setPlansLoading] = useState(true)
  const [plansError, setPlansError] = useState(false)
  const [interval, setInterval] = useState('monthly')
  const [loading, setLoading] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setPlansLoading(true)
    setPlansError(false)
    fetchPlans()
      .then(setPlans)
      .catch(() => setPlansError(true))
      .finally(() => setPlansLoading(false))
  }, [])

  const proPlan = plans.find(p => p.tier === 'pro' && p.interval === interval)
  const isFreeCurrent = !user || user.tier === 'free'
  const isProCurrent  = user?.tier === 'pro' && user?.subscription_status === 'active'

  async function handleSelect(planId) {
    setError('')
    setLoading(planId)
    try {
      const data = await initializePayment(planId)
      window.location.href = data.authorization_url
    } catch (e) {
      setError(e.message)
      setLoading(null)
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Current plan banner */}
      {user && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] px-4 py-3 flex items-center gap-3 flex-wrap">
          <div className="flex-1">
            <p className="text-sm text-[var(--text-h)] font-medium">
              You are on the <span className="capitalize font-bold">{user.tier}</span> plan
            </p>
            {user.subscription_expires_at && (
              <p className="text-xs text-[var(--text)] opacity-80">
                Renews {fmtDate(user.subscription_expires_at)}
              </p>
            )}
          </div>
          <span className={`text-xs font-semibold px-2 py-1 rounded border ${
            user.subscription_status === 'active'
              ? 'text-green-400 border-green-500/30 bg-green-500/10'
              : 'text-slate-400 border-slate-500/30 bg-slate-500/10'
          }`}>
            {user.subscription_status}
          </span>
        </div>
      )}

      {/* Interval toggle */}
      <div className="flex items-center gap-1 bg-[var(--code-bg)] rounded-lg p-1 w-fit">
        {['monthly', 'yearly'].map(i => (
          <button
            key={i}
            onClick={() => setInterval(i)}
            className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-colors capitalize ${
              interval === i
                ? 'bg-[var(--bg)] text-[var(--text-h)] shadow-sm border border-[var(--border)]'
                : 'text-[var(--text)] opacity-80 hover:opacity-100'
            }`}
          >
            {i}
          </button>
        ))}
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {/* Side-by-side comparison cards */}
      <div className="grid grid-cols-2 gap-4">
        <FreeCard isCurrent={isFreeCurrent} />
        {plansLoading ? (
          <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5 animate-pulse space-y-4">
            <div className="h-8 w-24 rounded-lg bg-[var(--border)]" />
            <div className="h-8 w-20 rounded bg-[var(--border)]" />
            <div className="space-y-2">
              {[...Array(4)].map((_, i) => <div key={i} className="h-3 rounded-full bg-[var(--border)]" />)}
            </div>
          </div>
        ) : plansError ? (
          <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5 flex items-center justify-center">
            <p className="text-xs text-[var(--text)] opacity-60 text-center">
              Could not load Pro plans.<br />
              <button onClick={() => window.location.reload()} className="text-[var(--accent)] hover:underline mt-1">Retry</button>
            </p>
          </div>
        ) : proPlan ? (
          <ProCard
            plan={proPlan}
            isCurrent={isProCurrent}
            onSelect={handleSelect}
            loading={loading}
          />
        ) : null}
      </div>

      <p className="text-xs text-[var(--text)] opacity-70 text-center">
        Payments processed securely by Paystack · Mobile money (Airtel, TNM) accepted
      </p>
    </div>
  )
}
