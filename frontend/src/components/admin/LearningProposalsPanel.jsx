import { useState, useEffect, useCallback } from 'react'
import { Brain, RefreshCw, X, ChevronDown, ChevronRight, Play } from 'lucide-react'
import { fetchLearningProposals, deactivateLearningProposal, triggerLossAnalysisPipeline, triggerStrategyPipeline } from '../../api/admin'

// Which pipeline owns each change_type
const PIPELINE_A_TYPES = new Set(['market_odds_ceiling', 'min_probability'])
const PIPELINE_B_TYPES = new Set(['market_suppression', 'league_suppression', 'kelly_fraction_adj', 'min_prob_by_agreement'])

// Human-readable labels for change_type values
const CHANGE_TYPE_LABEL = {
  market_odds_ceiling:   'Odds Ceiling',
  market_suppression:    'Market Suppressed',
  league_suppression:    'League Suppressed',
  kelly_fraction_adj:    'Kelly Adj.',
  min_prob_by_agreement: 'Min Probability',
}

const CHANGE_TYPE_COLOR = {
  market_odds_ceiling:   'text-blue-400 bg-blue-500/10 border-blue-500/20',
  market_suppression:    'text-red-400 bg-red-500/10 border-red-500/20',
  league_suppression:    'text-orange-400 bg-orange-500/10 border-orange-500/20',
  kelly_fraction_adj:    'text-purple-400 bg-purple-500/10 border-purple-500/20',
  min_prob_by_agreement: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
}

const CONF_COLOR = {
  High:   'text-emerald-400',
  Medium: 'text-amber-400',
  Low:    'text-slate-400',
}

function ProposalRow({ proposal, onDeactivate }) {
  const [expanded, setExpanded] = useState(false)
  const [deactivating, setDeactivating] = useState(false)

  const typeLabel = CHANGE_TYPE_LABEL[proposal.change_type] || proposal.change_type
  const typeColor = CHANGE_TYPE_COLOR[proposal.change_type] || 'text-slate-400 bg-slate-500/10 border-slate-500/20'
  const confColor = CONF_COLOR[proposal.confidence] || ''

  const valueStr =
    proposal.proposed_value != null
      ? String(proposal.proposed_value)
      : proposal.change_type.includes('suppression') ? 'suppressed' : '—'

  async function handleDeactivate(e) {
    e.stopPropagation()
    if (!confirm(`Override and deactivate this proposal for "${proposal.target}"?`)) return
    setDeactivating(true)
    try {
      await deactivateLearningProposal(proposal.id)
      onDeactivate(proposal.id)
    } catch (err) {
      alert(err.message)
    } finally {
      setDeactivating(false)
    }
  }

  const date = proposal.created_at
    ? new Date(proposal.created_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit' })
    : '—'

  return (
    <div className="border-t border-[var(--border)] first:border-0">
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-[var(--code-bg)] transition-colors text-left"
      >
        {expanded
          ? <ChevronDown size={12} className="text-[var(--text)] opacity-65 shrink-0" />
          : <ChevronRight size={12} className="text-[var(--text)] opacity-65 shrink-0" />
        }
        {/* Type badge */}
        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border shrink-0 ${typeColor}`}>
          {typeLabel}
        </span>
        {/* Target */}
        <span className="flex-1 text-xs font-medium text-[var(--text-h)] truncate">{proposal.target}</span>
        {/* Value */}
        <span className="text-xs text-[var(--text)] opacity-75 shrink-0 tabular-nums">{valueStr}</span>
        {/* Confidence */}
        {proposal.confidence && (
          <span className={`text-[10px] font-semibold shrink-0 ${confColor}`}>{proposal.confidence}</span>
        )}
        {/* Date */}
        <span className="text-[10px] text-[var(--text)] opacity-65 shrink-0">{date}</span>
        {/* Deactivate */}
        <button
          onClick={handleDeactivate}
          disabled={deactivating}
          className="shrink-0 p-1 rounded hover:bg-red-500/15 text-[var(--text)] opacity-30 hover:opacity-100 hover:text-red-400 transition-all disabled:opacity-20"
          title="Override / deactivate"
        >
          <X size={11} />
        </button>
      </button>

      {expanded && (
        <div className="px-10 pb-3 space-y-1.5">
          {proposal.rationale && (
            <div>
              <p className="text-[10px] font-semibold text-[var(--text)] opacity-70 uppercase tracking-wide mb-0.5">Rationale</p>
              <p className="text-xs text-[var(--text)] opacity-80 leading-relaxed">{proposal.rationale}</p>
            </div>
          )}
          {proposal.backtest_note && (
            <div>
              <p className="text-[10px] font-semibold text-[var(--text)] opacity-70 uppercase tracking-wide mb-0.5">Backtest</p>
              <p className="text-xs text-[var(--text)] opacity-80 leading-relaxed">{proposal.backtest_note}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Group proposals by change_type for display
function groupBy(proposals) {
  const order = [
    'market_suppression',
    'league_suppression',
    'market_odds_ceiling',
    'kelly_fraction_adj',
    'min_prob_by_agreement',
  ]
  const groups = {}
  for (const p of proposals) {
    const key = p.change_type
    if (!groups[key]) groups[key] = []
    groups[key].push(p)
  }
  // Sort by predefined order then alphabetical for unknown types
  return Object.entries(groups).sort(([a], [b]) => {
    const ia = order.indexOf(a)
    const ib = order.indexOf(b)
    if (ia === -1 && ib === -1) return a.localeCompare(b)
    if (ia === -1) return 1
    if (ib === -1) return -1
    return ia - ib
  })
}

export default function LearningProposalsPanel() {
  const [proposals, setProposals]   = useState([])
  const [showHistory, setShowHistory] = useState(false)
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState(null)
  const [runningA, setRunningA]     = useState(false)
  const [runningB, setRunningB]     = useState(false)
  const [runResultA, setRunResultA] = useState(null)
  const [runResultB, setRunResultB] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchLearningProposals({ activeOnly: !showHistory })
      setProposals(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [showHistory])

  useEffect(() => { load() }, [load])

  function handleDeactivated(id) {
    setProposals(prev => prev.filter(p => p.id !== id))
  }

  async function handleRunA() {
    setRunningA(true); setRunResultA(null)
    try {
      const r = await triggerLossAnalysisPipeline()
      setRunResultA(r)
      await load()
    } catch (e) { setRunResultA({ error: e.message }) }
    finally { setRunningA(false) }
  }

  async function handleRunB() {
    setRunningB(true); setRunResultB(null)
    try {
      const r = await triggerStrategyPipeline()
      setRunResultB(r)
      await load()
    } catch (e) { setRunResultB({ error: e.message }) }
    finally { setRunningB(false) }
  }

  const pipelineA = proposals.filter(p => PIPELINE_A_TYPES.has(p.change_type))
  const pipelineB = proposals.filter(p => PIPELINE_B_TYPES.has(p.change_type))
  const unknown   = proposals.filter(p => !PIPELINE_A_TYPES.has(p.change_type) && !PIPELINE_B_TYPES.has(p.change_type))

  const groupsA   = groupBy(pipelineA)
  const groupsB   = groupBy(pipelineB)
  const groupsUnk = groupBy(unknown)

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--border)]">
        <div className="flex items-center gap-2">
          <Brain size={13} className="text-[var(--accent)]" />
          <span className="text-xs font-semibold text-[var(--text-h)]">
            Self-Learning Proposals
          </span>
          {proposals.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] font-semibold">
              {proposals.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={showHistory}
              onChange={e => setShowHistory(e.target.checked)}
              className="w-3 h-3 accent-[var(--accent)]"
            />
            <span className="text-[10px] text-[var(--text)] opacity-80">Show history</span>
          </label>
          <button
            onClick={load}
            disabled={loading}
            className="text-[var(--text)] opacity-70 hover:opacity-100 transition-opacity disabled:opacity-30"
          >
            <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {error && (
        <p className="px-4 py-3 text-xs text-red-400">{error}</p>
      )}

      {!loading && proposals.length === 0 && (
        <p className="px-4 py-6 text-xs text-[var(--text)] opacity-70 italic text-center">
          No active proposals — run a pipeline below, or all proposals were overridden.
        </p>
      )}

      {/* ── Pipeline A — Loss Analysis ─────────────────────────────────────── */}
      <PipelineSection
        label="Pipeline A — Loss Analysis"
        sublabel="Odds ceilings and min-probability from settled losses"
        groups={groupsA}
        running={runningA}
        runResult={runResultA}
        onRun={handleRunA}
        onDeactivate={handleDeactivated}
        accentClass="text-blue-400 border-blue-500/20 bg-blue-500/5"
      />

      {/* ── Pipeline B — Strategy ──────────────────────────────────────────── */}
      <PipelineSection
        label="Pipeline B — Strategy"
        sublabel="Market/league suppression, Kelly adj, min-prob by agreement"
        groups={groupsB}
        running={runningB}
        runResult={runResultB}
        onRun={handleRunB}
        onDeactivate={handleDeactivated}
        accentClass="text-violet-400 border-violet-500/20 bg-violet-500/5"
      />

      {/* ── Unknown / future types ─────────────────────────────────────────── */}
      {groupsUnk.map(([changeType, rows]) => {
        const label = CHANGE_TYPE_LABEL[changeType] || changeType
        const color = CHANGE_TYPE_COLOR[changeType] || ''
        return (
          <div key={changeType}>
            <div className="px-4 py-1.5 flex items-center gap-2 bg-[var(--bg)]">
              <span className={`text-[10px] font-bold uppercase tracking-wider ${(color.match(/text-\S+/) || [])[0] || 'text-[var(--text)]'}`}>
                {label}
              </span>
              <span className="text-[10px] text-[var(--text)] opacity-65">
                {rows.length} proposal{rows.length !== 1 ? 's' : ''}
              </span>
            </div>
            {rows.map(p => (
              <ProposalRow key={p.id} proposal={p} onDeactivate={handleDeactivated} />
            ))}
          </div>
        )
      })}
    </div>
  )
}

function PipelineSection({ label, sublabel, groups, running, runResult, onRun, onDeactivate, accentClass }) {
  const total = groups.reduce((n, [, rows]) => n + rows.length, 0)
  return (
    <div className={`border-t border-[var(--border)]`}>
      {/* Pipeline header */}
      <div className={`flex items-center justify-between gap-3 px-4 py-2 border-b border-[var(--border)] ${accentClass}`}>
        <div>
          <p className="text-xs font-bold text-[var(--text-h)]">{label}</p>
          <p className="text-[10px] text-[var(--text)] opacity-80">{sublabel}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {total > 0 && (
            <span className="text-[10px] text-[var(--text)] opacity-70">{total} active</span>
          )}
          <button
            onClick={onRun}
            disabled={running}
            className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-[var(--border)] text-[10px] font-semibold text-[var(--text)] hover:text-[var(--accent)] hover:border-[var(--accent)] transition-colors disabled:opacity-40"
          >
            {running ? <RefreshCw size={10} className="animate-spin" /> : <Play size={10} />}
            {running ? 'Running…' : 'Run Now'}
          </button>
        </div>
      </div>

      {/* Run result */}
      {runResult && (
        <div className={`px-4 py-2 text-[10px] border-b border-[var(--border)] ${runResult.error ? 'text-red-400 bg-red-500/5' : 'text-green-400 bg-green-500/5'}`}>
          {runResult.error
            ? `Error: ${runResult.error}`
            : runResult.accepted_proposals != null
              ? `✓ ${runResult.bets_analysed} bets analysed · ${runResult.accepted_proposals} proposal${runResult.accepted_proposals !== 1 ? 's' : ''} accepted`
              : `✓ ${runResult.bets_analysed} bets · ${runResult.proposals_accepted} accepted`
          }
        </div>
      )}

      {/* Proposal rows grouped by change_type */}
      {groups.length === 0 && !running && (
        <p className="px-4 py-3 text-[10px] text-[var(--text)] opacity-65 italic">No active proposals for this pipeline.</p>
      )}
      {groups.map(([changeType, rows]) => {
        const label_ = CHANGE_TYPE_LABEL[changeType] || changeType
        const color  = CHANGE_TYPE_COLOR[changeType] || ''
        return (
          <div key={changeType}>
            <div className="px-4 py-1.5 flex items-center gap-2 bg-[var(--bg)]">
              <span className={`text-[10px] font-bold uppercase tracking-wider ${(color.match(/text-\S+/) || [])[0] || 'text-[var(--text)]'}`}>
                {label_}
              </span>
              <span className="text-[10px] text-[var(--text)] opacity-65">
                {rows.length} proposal{rows.length !== 1 ? 's' : ''}
              </span>
            </div>
            {rows.map(p => (
              <ProposalRow key={p.id} proposal={p} onDeactivate={onDeactivate} />
            ))}
          </div>
        )
      })}
    </div>
  )
}
