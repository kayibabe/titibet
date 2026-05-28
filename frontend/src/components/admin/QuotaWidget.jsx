import { useState, useEffect } from 'react'
import { RefreshCw, Zap } from 'lucide-react'
import { fetchApiQuota } from '../../api/admin'

function QuotaBar({ remaining, limit }) {
  if (limit == null || remaining == null) return null
  const pct = Math.max(0, Math.min(100, (remaining / limit) * 100))
  const color =
    pct > 30 ? 'bg-emerald-500' :
    pct > 10 ? 'bg-amber-500' :
               'bg-red-500'
  return (
    <div className="w-full h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
      <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

export default function QuotaWidget() {
  const [quota, setQuota]     = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      setQuota(await fetchApiQuota())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const remaining = quota?.remaining
  const limit     = quota?.limit
  const pctUsed   = quota?.pct_used

  const statusColor =
    remaining == null       ? 'text-[var(--text)]' :
    remaining <= 5          ? 'text-red-400' :
    remaining <= 20         ? 'text-amber-400' :
                              'text-emerald-400'

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <Zap size={13} className="text-[var(--accent)]" />
          <span className="text-xs font-semibold text-[var(--text-h)]">API-Football Quota</span>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="text-[var(--text)] opacity-70 hover:opacity-100 transition-opacity disabled:opacity-30"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && <p className="text-[10px] text-red-400">{error}</p>}

      {quota && (
        <>
          <div className="flex items-end gap-1.5 mb-2">
            <span className={`text-2xl font-bold tabular-nums ${statusColor}`}>
              {remaining ?? '—'}
            </span>
            <span className="text-xs text-[var(--text)] opacity-80 pb-0.5">
              / {limit ?? '—'} remaining
            </span>
            {pctUsed != null && (
              <span className="text-[10px] text-[var(--text)] opacity-65 pb-0.5 ml-auto">
                {pctUsed}% used
              </span>
            )}
          </div>
          <QuotaBar remaining={remaining} limit={limit} />
          <p className="text-[10px] text-[var(--text)] opacity-65 mt-1.5">{quota.reset_note}</p>
        </>
      )}

      {!quota && !loading && !error && (
        <p className="text-xs text-[var(--text)] opacity-70">No API calls made yet this session.</p>
      )}
    </div>
  )
}
