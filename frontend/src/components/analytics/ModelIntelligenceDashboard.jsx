import { useState, useEffect } from 'react'
import { Brain, ChevronDown, ChevronUp, Clock, TrendingDown, TrendingUp, CheckCircle2, XCircle } from 'lucide-react'
import { fetchModelIntelligence } from '../../api/analytics'

// Change-type display config
const CHANGE_TYPE_META = {
  market_odds_ceiling: {
    label: 'Odds Ceiling',
    description: 'Maximum odds the system will accept for this market in accumulators',
    color: 'text-amber-400',
    bg: 'bg-amber-500/10 border-amber-500/25',
    icon: TrendingDown,
  },
  market_suppression: {
    label: 'Market Suppressed',
    description: 'This market is excluded from accumulator candidates due to poor performance',
    color: 'text-red-400',
    bg: 'bg-red-500/10 border-red-500/25',
    icon: XCircle,
  },
  league_suppression: {
    label: 'League Suppressed',
    description: 'Signals from this league are excluded from accumulators',
    color: 'text-red-400',
    bg: 'bg-red-500/10 border-red-500/25',
    icon: XCircle,
  },
  kelly_fraction_adj: {
    label: 'Kelly Adjustment',
    description: 'Kelly stake fraction adjusted for this confidence tier',
    color: 'text-blue-400',
    bg: 'bg-blue-500/10 border-blue-500/25',
    icon: TrendingUp,
  },
  min_prob_by_agreement: {
    label: 'Min Probability',
    description: 'Minimum model probability raised for this agreement type',
    color: 'text-violet-400',
    bg: 'bg-violet-500/10 border-violet-500/25',
    icon: TrendingUp,
  },
}

function ConfidencePill({ confidence }) {
  if (!confidence) return null
  const map = {
    High:   'bg-green-500/15 text-green-400 border-green-500/30',
    Medium: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    Low:    'bg-slate-500/10 text-slate-400 border-slate-500/25',
  }
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold border ${map[confidence] || map.Low}`}>
      {confidence} confidence
    </span>
  )
}

function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

function ProposalCard({ proposal, dim = false }) {
  const [expanded, setExpanded] = useState(false)
  const meta = CHANGE_TYPE_META[proposal.change_type] || {
    label: proposal.change_type,
    description: '',
    color: 'text-slate-400',
    bg: 'bg-slate-500/10 border-slate-500/25',
    icon: Brain,
  }
  const Icon = meta.icon

  return (
    <div className={`rounded-xl border ${meta.bg} overflow-hidden transition-opacity ${dim ? 'opacity-50' : ''}`}>
      <div className="px-4 py-3 space-y-2">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <Icon size={14} className={`shrink-0 ${meta.color}`} />
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`text-xs font-bold ${meta.color}`}>{meta.label}</span>
                <span className="text-xs font-semibold text-[var(--text-h)] truncate">{proposal.target}</span>
              </div>
              {meta.description && (
                <p className="text-[10px] text-[var(--text)] opacity-60 mt-0.5">{meta.description}</p>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {proposal.proposed_value != null && (
              <span className={`text-sm font-black tabular-nums ${meta.color}`}>
                {proposal.proposed_value.toFixed(2)}
              </span>
            )}
            <ConfidencePill confidence={proposal.confidence} />
          </div>
        </div>

        {/* Metadata row */}
        <div className="flex items-center gap-3 text-[10px] text-[var(--text)] opacity-60">
          <span className="flex items-center gap-1">
            <Clock size={9} />
            {fmtDate(proposal.created_at)}
          </span>
          <span className="flex items-center gap-1">
            {proposal.is_active
              ? <><CheckCircle2 size={9} className="text-green-400" /><span className="text-green-400">Active</span></>
              : <><XCircle size={9} className="text-slate-500" /><span>Superseded</span></>
            }
          </span>
        </div>

        {/* Expandable rationale + backtest note */}
        {(proposal.rationale || proposal.backtest_note) && (
          <>
            <button
              onClick={() => setExpanded(v => !v)}
              className="flex items-center gap-1 text-[10px] text-[var(--text)] opacity-50 hover:opacity-100 transition-opacity"
            >
              {expanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
              {expanded ? 'Hide reasoning' : 'Show reasoning'}
            </button>
            {expanded && (
              <div className="space-y-2 pt-1 border-t border-[var(--border)]">
                {proposal.rationale && (
                  <div>
                    <p className="text-[10px] font-semibold text-[var(--text-h)] mb-0.5">Why this change</p>
                    <p className="text-xs text-[var(--text)] opacity-85 leading-relaxed">{proposal.rationale}</p>
                  </div>
                )}
                {proposal.backtest_note && (
                  <div>
                    <p className="text-[10px] font-semibold text-[var(--text-h)] mb-0.5">Backtest result</p>
                    <p className="text-xs text-[var(--text)] opacity-85 leading-relaxed">{proposal.backtest_note}</p>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export default function ModelIntelligenceDashboard() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showHistory, setShowHistory] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetchModelIntelligence()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="space-y-3 animate-pulse">
        {[1, 2, 3].map(i => (
          <div key={i} className="h-24 rounded-xl bg-[var(--border)]" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
        Could not load model intelligence: {error}
      </div>
    )
  }

  const active = data?.active || []
  const history = data?.history || []

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Brain size={16} className="text-[var(--accent)]" />
            <h3 className="text-sm font-semibold text-[var(--text-h)]">Model Intelligence</h3>
            {active.length > 0 && (
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-[var(--accent)] text-white">
                {active.length} active
              </span>
            )}
          </div>
          <p className="text-xs text-[var(--text)] opacity-70 mt-1">
            Threshold changes the self-learning pipeline has validated and applied.
            Each row represents a concrete decision backed by historical backtest results.
          </p>
        </div>
      </div>

      {/* Active proposals */}
      {active.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg)] p-8 flex flex-col items-center gap-3 text-center">
          <Brain size={28} className="text-[var(--text)] opacity-30" />
          <p className="text-sm font-semibold text-[var(--text-h)]">No active learning decisions yet</p>
          <p className="text-xs text-[var(--text)] opacity-70 max-w-xs">
            The self-learning pipeline runs automatically after bets are settled.
            Once enough loss patterns are detected and backtested, active rules will appear here.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {active.map(p => (
            <ProposalCard key={p.id} proposal={p} />
          ))}
        </div>
      )}

      {/* History section */}
      {history.length > 0 && (
        <div className="space-y-2">
          <button
            onClick={() => setShowHistory(v => !v)}
            className="flex items-center gap-1.5 text-xs text-[var(--text)] opacity-60 hover:opacity-100 transition-opacity"
          >
            {showHistory ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {showHistory ? 'Hide' : 'Show'} superseded decisions ({history.length})
          </button>
          {showHistory && (
            <div className="space-y-2">
              {history.map(p => (
                <ProposalCard key={p.id} proposal={p} dim />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
