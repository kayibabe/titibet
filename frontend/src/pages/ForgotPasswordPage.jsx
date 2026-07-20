import { useState } from 'react'
import { ArrowLeft } from 'lucide-react'

export default function ForgotPasswordPage({ onBack }) {
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Request failed')
      }
      setSent(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg-page)] px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-[var(--text-h)]">TiTiBet</h1>
          <p className="text-sm text-[var(--text)] opacity-75 mt-1">Reset your password</p>
        </div>

        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-6">
          {sent ? (
            <div className="text-center space-y-3">
              <p className="text-sm text-[var(--text-h)] font-medium">Check your inbox</p>
              <p className="text-sm text-[var(--text)] opacity-75">
                If <span className="text-[var(--text-h)]">{email}</span> is registered, a reset link is on its way. Check your spam folder if it doesn't arrive within a few minutes.
              </p>
              <button onClick={onBack} className="text-sm text-[var(--accent)] hover:underline mt-2">
                Back to sign in
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <p className="text-sm text-[var(--text)] opacity-75">
                Enter your email and we'll send you a link to reset your password.
              </p>
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-[var(--text-h)]">Email</label>
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
                  placeholder="you@example.com"
                />
              </div>

              {error && (
                <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={loading}
                className="w-full py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {loading ? 'Sending…' : 'Send Reset Link'}
              </button>
            </form>
          )}
        </div>

        {!sent && (
          <button onClick={onBack} className="flex items-center gap-1.5 mx-auto mt-4 text-sm text-[var(--text)] opacity-75 hover:opacity-100 transition-opacity">
            <ArrowLeft size={13} />
            Back to sign in
          </button>
        )}
      </div>
    </div>
  )
}
