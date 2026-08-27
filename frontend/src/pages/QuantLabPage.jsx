import { useEffect, useState } from 'react'
import { Activity, RefreshCw, ShieldCheck, TrendingDown, TrendingUp } from 'lucide-react'
import { fetchEngineComparison } from '../api/quantLab'

function Metric({ label, value }) {
  return (
    <div className="rounded-lg bg-[var(--code-bg)] border border-[var(--border)] p-3">
      <div className="text-[10px] uppercase tracking-wide text-[var(--text)] opacity-70">{label}</div>
      <div className="mt-1 text-sm font-semibold text-[var(--text-h)] tabular-nums">{value}</div>
    </div>
  )
}

function fmtPct(v, digits = 1) {
  return v == null ? '—' : `${Number(v * (Math.abs(v) <= 1 ? 100 : 1)).toFixed(digits)}%`
}

function EngineCard({ item }) {
  const eligible = item.eligible_for_comparison
  const roiPositive = (item.roi ?? 0) > 0
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Activity size={14} className="text-[var(--accent)]" />
          <span className="font-semibold text-[var(--text-h)] capitalize">{item.engine}</span>
        </div>
        <span className={`text-[10px] font-semibold px-2 py-1 rounded border ${eligible ? 'text-green-400 border-green-500/30 bg-green-500/10' : 'text-amber-400 border-amber-500/30 bg-amber-500/10'}`}>
          {eligible ? 'Comparable' : `Need ${item.n ? '' : 'more '}sample`}
        </span>
      </div>
      <div className="p-4 grid grid-cols-2 sm:grid-cols-3 gap-3">
        <Metric label="Bets" value={item.n ?? 0} />
        <Metric label="Hit rate" value={fmtPct(item.hit_rate)} />
        <Metric label="ROI" value={<span className={roiPositive ? 'text-green-400' : 'text-red-400'}>{fmtPct(item.roi)}</span>} />
        <Metric label="Brier" value={item.brier == null ? '—' : item.brier.toFixed(4)} />
        <Metric label="Log loss" value={item.log_loss == null ? '—' : item.log_loss.toFixed(4)} />
        <Metric label="Calibration" value={fmtPct(item.calibration_error)} />
        <Metric label="Mean EV" value={fmtPct(item.mean_ev)} />
        <Metric label="Positive EV" value={fmtPct(item.positive_ev_rate)} />
        <Metric label="Mean model P" value={fmtPct(item.mean_model_probability)} />
      </div>
      <div className="px-4 pb-4 text-xs text-[var(--text)] opacity-75">
        95% hit-rate CI: {item.hit_rate_ci ? `${fmtPct(item.hit_rate_ci[0])} – ${fmtPct(item.hit_rate_ci[1])}` : '—'}
      </div>
    </div>
  )
}

export default function QuantLabPage() {
  const [rows, setRows] = useState([])
  const [market, setMarket] = useState('')
  const [minN, setMinN] = useState(30)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load() {
    setLoading(true)
    setError('')
    try {
      setRows(await fetchEngineComparison({ market, minN }))
    } catch (e) {
      setError(e.message || 'Unable to load quantitative diagnostics')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] p-4">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck size={16} className="text-[var(--accent)]" />
              <h2 className="text-sm font-bold text-[var(--text-h)]">Quant Lab</h2>
            </div>
            <p className="mt-1 text-xs text-[var(--text)] opacity-75 max-w-2xl">
              Compare prediction quality and betting economics without changing production strategy. Lower Brier/log-loss/calibration error is better; positive ROI is secondary to probability quality.
            </p>
          </div>
          <button onClick={load} disabled={loading} className="inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-[var(--border)] text-xs font-semibold text-[var(--text-h)] hover:bg-[var(--bg)] disabled:opacity-50">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <select value={market} onChange={e => setMarket(e.target.value)} className="bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs text-[var(--text-h)]">
            <option value="">All markets</option>
            <option value="Over 1.5">Over 1.5</option>
            <option value="Over 2.5">Over 2.5</option>
            <option value="Under 2.5">Under 2.5</option>
            <option value="Home Over 0.5">Home Over 0.5</option>
            <option value="Away Over 0.5">Away Over 0.5</option>
            <option value="Double Chance">Double Chance</option>
          </select>
          <select value={minN} onChange={e => setMinN(Number(e.target.value))} className="bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2 text-xs text-[var(--text-h)]">
            <option value={10}>Minimum 10 bets</option>
            <option value={30}>Minimum 30 bets</option>
            <option value={50}>Minimum 50 bets</option>
            <option value={100}>Minimum 100 bets</option>
          </select>
          <button onClick={load} className="px-3 py-2 rounded-lg bg-[var(--accent)] text-white text-xs font-semibold hover:opacity-90">Compare</button>
        </div>
      </div>

      {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-xs text-red-300">{error}</div>}

      {loading ? (
        <div className="rounded-xl border border-[var(--border)] p-8 text-center text-sm text-[var(--text)] animate-pulse">Loading quantitative diagnostics…</div>
      ) : rows.length === 0 ? (
        <div className="rounded-xl border border-[var(--border)] p-8 text-center text-sm text-[var(--text)]">No persisted engine results are available for this scope.</div>
      ) : (
        <div className="grid xl:grid-cols-3 gap-4">
          {rows.map(item => <EngineCard key={item.engine} item={item} />)}
        </div>
      )}

      {!loading && rows.length > 0 && (
        <div className="grid md:grid-cols-2 gap-4">
          <div className="rounded-xl border border-[var(--border)] p-4">
            <div className="flex items-center gap-2 text-xs font-semibold text-[var(--text-h)]"><TrendingUp size={13} className="text-green-400" /> Promotion rule</div>
            <p className="mt-2 text-xs leading-5 text-[var(--text)] opacity-80">Do not promote an engine on ROI alone. Require adequate sample size, acceptable calibration, out-of-sample performance, and positive economic value.</p>
          </div>
          <div className="rounded-xl border border-[var(--border)] p-4">
            <div className="flex items-center gap-2 text-xs font-semibold text-[var(--text-h)]"><TrendingDown size={13} className="text-amber-400" /> Research warning</div>
            <p className="mt-2 text-xs leading-5 text-[var(--text)] opacity-80">The comparison is only as strong as the historical replay. Strict point-in-time runs should be used before any live rule change.</p>
          </div>
        </div>
      )}
    </div>
  )
}
