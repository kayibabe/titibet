import { useState } from 'react'
import { Eye, EyeOff, ArrowLeft } from 'lucide-react'
import { useAuth } from '../context/AuthContext'

export default function RegisterPage({ onSwitch, onBack }) {
  const { register } = useAuth()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    if (password.length < 8) {
      setError('Password must be at least 8 characters')
      return
    }
    setLoading(true)
    try {
      await register(email, password, name || null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const pwStrength = password.length === 0
    ? null
    : password.length < 8
      ? { label: 'Too short', color: 'bg-red-500', width: '25%', text: 'text-red-400' }
      : password.length < 12
        ? { label: 'Fair', color: 'bg-amber-500', width: '50%', text: 'text-amber-400' }
        : password.length < 16
          ? { label: 'Good', color: 'bg-blue-500', width: '75%', text: 'text-blue-400' }
          : { label: 'Strong', color: 'bg-green-500', width: '100%', text: 'text-green-400' }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg-page)] px-4">
      <div className="w-full max-w-sm">
        {onBack && (
          <button onClick={onBack} className="flex items-center gap-1.5 text-xs text-[var(--text)] opacity-60 hover:opacity-100 transition-opacity mb-6 cursor-pointer">
            <ArrowLeft size={13} /> Back to home
          </button>
        )}
        <div className="mb-8 text-center">
          <div className="flex flex-col items-center gap-1 mb-1">
            <div className="flex items-center justify-center gap-2">
              <img src="/falcon.png" alt="TiTiBet" style={{ width: '44px', height: '44px', objectFit: 'contain' }} />
              <span className="text-2xl font-bold text-[var(--text-h)] tracking-tight">TiTiBet</span>
            </div>
            <span className="block text-[10px] text-[var(--accent)] font-semibold tracking-widest uppercase opacity-80">
              Intelligence Platform
            </span>
          </div>
          <p className="text-sm text-[var(--text)] opacity-75 mt-2">Create your free account</p>
        </div>

        <form onSubmit={handleSubmit} className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-6 space-y-4 shadow-[var(--shadow-card)]">
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-[var(--text-h)]">
              Name <span className="opacity-60 font-normal text-xs">(optional)</span>
            </label>
            <input
              type="text"
              autoComplete="name"
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)] transition-colors"
              placeholder="John Doe"
            />
          </div>
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-[var(--text-h)]">Email</label>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              className="w-full px-3 py-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)] transition-colors"
              placeholder="you@example.com"
            />
          </div>
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-[var(--text-h)]">Password</label>
            <div className="relative">
              <input
                type={showPw ? 'text' : 'password'}
                required
                autoComplete="new-password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full px-3 py-2.5 pr-10 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)] transition-colors"
                placeholder="At least 8 characters"
              />
              <button
                type="button"
                onClick={() => setShowPw(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text)] opacity-50 hover:opacity-100 transition-opacity"
                tabIndex={-1}
                aria-label={showPw ? 'Hide password' : 'Show password'}
              >
                {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
            {/* Password strength meter */}
            {pwStrength && (
              <div className="space-y-1">
                <div className="h-1 rounded-full bg-[var(--border)] overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-300 ${pwStrength.color}`}
                    style={{ width: pwStrength.width }}
                  />
                </div>
                <p className={`text-[11px] ${pwStrength.text}`}>{pwStrength.label}</p>
              </div>
            )}
          </div>

          {error && (
            <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 active:scale-[0.99] disabled:opacity-50 transition-all"
          >
            {loading ? 'Creating account…' : 'Create Account'}
          </button>
        </form>

        <p className="text-center text-sm text-[var(--text)] opacity-75 mt-4">
          Already have an account?{' '}
          <button onClick={onSwitch} className="text-[var(--accent)] hover:underline font-medium">
            Sign in
          </button>
        </p>
      </div>
    </div>
  )
}
