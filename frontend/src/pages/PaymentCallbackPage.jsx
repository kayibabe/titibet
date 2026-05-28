import { useEffect, useState } from 'react'
import { CheckCircle, XCircle, Loader } from 'lucide-react'
import { verifyPayment } from '../api/payments'
import { useAuth } from '../context/AuthContext'

export default function PaymentCallbackPage({ onDone }) {
  const { token, user } = useAuth()
  const [status, setStatus] = useState('verifying') // verifying | success | error
  const [tier, setTier] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const reference = params.get('reference') || params.get('trxref')
    if (!reference) {
      setStatus('error')
      setError('No payment reference found in URL.')
      return
    }
    verifyPayment(reference)
      .then(data => {
        setTier(data.tier)
        setStatus('success')
        // Reload user context after 1.5s then navigate home
        setTimeout(() => onDone?.(), 2000)
      })
      .catch(e => {
        setStatus('error')
        setError(e.message)
      })
  }, [])

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg-page)] px-4">
      <div className="w-full max-w-sm text-center space-y-4">
        {status === 'verifying' && (
          <>
            <Loader size={40} className="mx-auto text-[var(--accent)] animate-spin" />
            <p className="text-sm text-[var(--text)] opacity-75">Verifying your payment…</p>
          </>
        )}
        {status === 'success' && (
          <>
            <CheckCircle size={48} className="mx-auto text-green-400" />
            <h2 className="text-xl font-bold text-[var(--text-h)] capitalize">
              Welcome to {tier}!
            </h2>
            <p className="text-sm text-[var(--text)] opacity-75">
              Your subscription is now active. Redirecting you back…
            </p>
          </>
        )}
        {status === 'error' && (
          <>
            <XCircle size={48} className="mx-auto text-red-400" />
            <h2 className="text-xl font-bold text-[var(--text-h)]">Verification failed</h2>
            <p className="text-sm text-red-400">{error}</p>
            <button
              onClick={() => onDone?.()}
              className="px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold"
            >
              Go back
            </button>
          </>
        )}
      </div>
    </div>
  )
}
