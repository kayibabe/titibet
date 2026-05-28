// Engine breakdown — side-by-side Bayesian vs Poisson detail panel shown when
// the user expands a SignalCard. Reads from the nested API shape produced by
// backend/app/routers/signals.py::_to_signal_out (signal.bayesian / signal.poisson).
//
// IMPORTANT: do NOT introduce flat property names like `signal.bayesian_prob` or
// `signal.bayesian_best_odd`. The API contract is nested-only — flat mirrors were
// dropped to avoid the prior dual-shape bug (see git history for context).

function Row({ label, value, highlight, tone }) {
  const toneCls =
    tone === 'good' ? 'text-green-400' :
    tone === 'warn' ? 'text-amber-400' :
    tone === 'bad'  ? 'text-red-400'   :
    highlight       ? 'text-[var(--accent)] font-semibold'
                    : 'text-[var(--text-h)]'
  return (
    <div className="flex justify-between items-center py-0.5 gap-2">
      <span className="text-[var(--text)] text-xs">{label}</span>
      <span className={`text-xs font-mono ${toneCls} text-right truncate`}>
        {value ?? '—'}
      </span>
    </div>
  )
}

const fmtPct = (x, digits = 1) =>
  x == null || Number.isNaN(x) ? null : `${(x * 100).toFixed(digits)}%`

const fmtPctRaw = (x, digits = 1) =>
  x == null || Number.isNaN(x) ? null : `${x.toFixed(digits)}%`

const fmtNum = (x, digits = 2) =>
  x == null || Number.isNaN(x) ? null : x.toFixed(digits)

function EmptyEngine({ name, accent, reason }) {
  return (
    <div className={`rounded-lg border border-[var(--border)] px-3 py-2 ${accent.bg}`}>
      <div className={`text-xs font-semibold ${accent.text} mb-1`}>{name}</div>
      <div className="text-xs text-[var(--text)] opacity-70 italic py-2">
        {reason}
      </div>
    </div>
  )
}

export default function EngineBreakdown({ signal }) {
  const b = signal.bayesian
  const p = signal.poisson

  // ── Bayesian derived values ─────────────────────────────────────────────
  const bayesianProb   = fmtPct(b?.prob)
  const bayesianEdge   = fmtPct(b?.edge)        // edge stored as 0-1 decimal
  const bayesianEv     = b?.ev_pct != null ? `${b.ev_pct >= 0 ? '+' : ''}${b.ev_pct.toFixed(1)}%` : null
  const bayesianKelly  = b?.kelly_pct != null ? `${(b.kelly_pct * 100).toFixed(2)}%` : null
  const bayesianMargin = b?.overround != null ? `${((b.overround - 1) * 100).toFixed(1)}%` : null
  const bayesianQS     = b?.quality_score != null ? (b.quality_score * 100).toFixed(0) : null

  // ── Poisson derived values ──────────────────────────────────────────────
  const poissonProb   = fmtPct(p?.prob)
  const poissonEdge   = fmtPctRaw(p?.edge_pct)  // edge_pct stored as percent already
  const poissonLambda = p?.lambda_h != null && p?.lambda_a != null
    ? `${p.lambda_h.toFixed(2)} / ${p.lambda_a.toFixed(2)}`
    : null
  const poissonTotal  = fmtNum(p?.lambda_total)

  // ── Tones ───────────────────────────────────────────────────────────────
  const evTone =
    b?.ev_pct == null ? undefined :
    b.ev_pct > 5      ? 'good'    :
    b.ev_pct > 0      ? 'warn'    : 'bad'

  const marginTone =
    b?.overround == null ? undefined :
    b.overround < 1.05   ? 'good'    :
    b.overround < 1.10   ? undefined : 'warn'

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
      {/* ── Bayesian ── */}
      {b ? (
        <div className="rounded-lg border border-[var(--border)] px-3 py-2 bg-blue-500/5">
          <div className="text-xs font-semibold text-blue-400 mb-1">Bayesian</div>
          <Row label="Prob"        value={bayesianProb} />
          <Row label="Edge"        value={bayesianEdge} highlight={b.is_value} />
          <Row label="EV"          value={bayesianEv}   tone={evTone} />
          <Row label="Best odds"   value={fmtNum(b.best_odd)} />
          <Row label="Bookmaker"   value={b.bookmaker} />
          <Row label="Books"       value={b.bookmaker_count} />
          <Row label="Margin"      value={bayesianMargin} tone={marginTone} />
          <Row label="Kelly"       value={bayesianKelly} />
          <Row label="Confidence"  value={b.confidence} />
          {bayesianQS && <Row label="Quality" value={bayesianQS} />}
        </div>
      ) : (
        <EmptyEngine
          name="Bayesian"
          accent={{ bg: 'bg-blue-500/5', text: 'text-blue-400' }}
          reason="No Bayesian signal — insufficient bookmaker coverage for this market."
        />
      )}

      {/* ── Poisson ── */}
      {p ? (
        <div className="rounded-lg border border-[var(--border)] px-3 py-2 bg-purple-500/5">
          <div className="text-xs font-semibold text-purple-400 mb-1">Poisson</div>
          <Row label="Prob"        value={poissonProb} />
          <Row label="Edge"        value={poissonEdge} highlight={p.rule_strong} />
          <Row label="λH / λA"     value={poissonLambda} />
          <Row label="λ total"     value={poissonTotal} />
          <Row label="Grade"       value={p.grade} />
          <Row label="Rule"        value={p.rule_key} />
          <Row label="Rule pass"   value={p.rule_pass == null ? null : (p.rule_pass ? 'Yes' : 'No')}
                                   tone={p.rule_pass === true ? 'good' : p.rule_pass === false ? 'bad' : undefined} />
        </div>
      ) : (
        <EmptyEngine
          name="Poisson"
          accent={{ bg: 'bg-purple-500/5', text: 'text-purple-400' }}
          reason="No Poisson signal — market not covered by the Poisson rule set, or λ unavailable for these teams."
        />
      )}
    </div>
  )
}
