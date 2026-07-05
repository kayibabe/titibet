import { useState, useEffect } from 'react'
import { Check, Zap, Shield, Star } from 'lucide-react'
import { fetchPlans, initializePayment } from '../api/payments'
import { useAuth } from '../context/AuthContext'
import { fmtDate } from '../utils/format'

const TIER_ICON = { pro: Zap }
const TIER_COLOR = {
  pro: { accent: 'text-blue-400', border: 'border-blue-500/30', bg: 'bg-blue-500/10', btn: 'bg-blue-600 hover:bg-blue-500' },
}

function fmtMWK(tambala) {
  return `K${(tambala).toLocaleString()}`
}

function PlanCard({ plan, current, onSelect, loading }) {
  const c = TIER_COLOR[plan.tier] || TIER_COLOR.pro
  const Icon = TIER_ICON[plan.tier] || Star
  const isCurrent = current?.tier === plan.tier && current?.subscription_status === 'active'

  return (
    <div className={`rounded-xl border ${c.border} ${c.bg} p-5 flex flex-col gap-4 relative`}>
      {plan.interval === 'yearly' && (
        <span className="absolute top-3 right-3 text-[10px] font-bold text-green-400 bg-green-500/15 border border-green-500/30 px-2 py-0.5 rounded tracking-wide">
          SAVE 2 MONTHS
        </span>
      )}

      <div className="flex items-center gap-2">
        <div className={`w-8 h-8 rounded-lg ${c.bg} border ${c.border} flex items-center justify-center`}>
          <Icon size={15} className={c.accent} />
        </div>
        <div>
          <p className={`text-sm font-bold ${c.accent}`}>{plan.label}</p>
          <p className="text-[10px] text-[var(--text)] opacity-80 capitalize">{plan.interval}</p>
        </div>
      </div>

      <div>
        <span className="text-2xl font-bold text-[var(--text-h)]">{fmtMWK(plan.price_mwk)}</span>
        <span className="text-xs text-[var(--text)] opacity-80 ml-1">/ {plan.interval === 'yearly' ? 'year' : 'month'}</span>
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
          className={`w-full py-2 rounded-lg text-white text-xs font-semibold transition-colors ${c.btn} disabled:opacity-50`}
        >
          {loading === plan.id ? 'Redirecting…' : `Upgrade to ${plan.label}`}
        </button>
      )}
    </div>
  )
}

export default function PricingPage() {
  const { user } = useAuth()
  const [plans, setPlans] = useState([])
  const [interval, setInterval] = useState('monthly')
  const [loading, setLoading] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchPlans().then(setPlans).catch(() => {})
  }, [])

  const filtered = plans.filter(p => p.interval === interval)

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

      {/* Free tier */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5">
        <div className="flex items-center gap-2 mb-3">
          <Star size={15} className="text-[var(--text)] opacity-70" />
          <p className="text-sm font-bold text-[var(--text-h)]">Free</p>
          <span className="text-[10px] text-[var(--text)] opacity-70 ml-1">Forever</span>
        </div>
        <ul className="grid grid-cols-2 gap-1.5">
          {['Value signals (limited view)', 'Basic bet tracker', 'Market analytics'].map((f, i) => (
            <li key={i} className="flex items-center gap-1.5 text-xs text-[var(--text)] opacity-75">
              <Check size={10} className="text-[var(--text)] opacity-65 shrink-0" />
              {f}
            </li>
          ))}
        </ul>
      </div>

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

      {/* Plan cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {filtered.map(plan => (
          <PlanCard
            key={plan.id}
            plan={plan}
            current={user}
            onSelect={handleSelect}
            loading={loading}
          />
        ))}
      </div>

      <p className="text-xs text-[var(--text)] opacity-70 text-center">
        Payments processed securely by Paystack · Mobile money (Airtel, TNM) accepted
      </p>
    </div>
  )
}
