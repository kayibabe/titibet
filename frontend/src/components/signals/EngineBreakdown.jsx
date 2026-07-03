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
  const adv = signal.advanced

  // ── Bayesian derived values ─────────────────────────────────────────────
  const bayesianProb   = fmtPct(b?.prob)
  const bayesianEdge   = fmtPct(b?.edge)        // edge stored as 0-1 decimal
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
  const marginTone =
    b?.overround == null ? undefined :
    b.overround < 1.05   ? 'good'    :
    b.overround < 1.10   ? undefined : 'warn'

  // ── Advanced model derived values ──────────────────────────────────────────
  const zinbLambda = adv?.zinb_lambda_h != null && adv?.zinb_lambda_a != null
    ? `${adv.zinb_lambda_h.toFixed(2)} / ${adv.zinb_lambda_a.toFixed(2)}`
    : null
  const zinbTotal = adv?.zinb_lambda_h != null && adv?.zinb_lambda_a != null
    ? (adv.zinb_lambda_h + adv.zinb_lambda_a).toFixed(2)
    : null
  const glickoDisplay = adv?.glicko_r_diff != null
    ? `${adv.glicko_r_diff >= 0 ? '+' : ''}${adv.glicko_r_diff.toFixed(0)}`
    : null
  const glickoTone = adv?.glicko_r_diff == null ? undefined
    : adv.glicko_r_diff > 100 ? 'good' : adv.glicko_r_diff < -100 ? 'bad' : undefined
  const breaRiDisplay = adv?.brea_ri1 != null ? `${(adv.brea_ri1 * 100).toFixed(1)}%` : null
  const breaRiTone = adv?.brea_ri1 == null ? undefined
    : adv.brea_ri1 < 0.07 ? 'good' : adv.brea_ri1 < 0.10 ? 'warn' : 'bad'
  const fhgiPDisplay = adv?.fhgi_p_model != null ? fmtPct(adv.fhgi_p_model) : null
  const fhgiPTone = adv?.fhgi_p_model == null ? undefined
    : adv.fhgi_p_model > 0.60 ? 'good' : adv.fhgi_p_model > 0.50 ? 'warn' : 'bad'

  const hasAdvanced = adv && Object.values(adv).some(v => v != null)

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
      {/* ── Bayesian ── */}
      {b ? (
        <div className="rounded-lg border border-[var(--border)] px-3 py-2 bg-blue-500/5">
          <div className="text-xs font-semibold text-blue-400 mb-1">Bayesian</div>
          <Row label="Prob"        value={bayesianProb} />
          <Row label="Edge"        value={bayesianEdge} highlight={b.is_value} />
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

      {/* ── Advanced Models — full-width when present ── */}
      {hasAdvanced && (
        <div className="md:col-span-2 rounded-lg border border-[var(--border)] px-3 py-2 bg-teal-500/5">
          <div className="text-xs font-semibold text-teal-400 mb-2">Advanced Models</div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6">
            {/* ZINB */}
            <div>
              <div className="text-[10px] text-[var(--text)] opacity-50 uppercase tracking-wider mb-1">ZINB xG</div>
              <Row label="λH / λA (ZINB)" value={zinbLambda} />
              <Row label="λ total"         value={zinbTotal} />
            </div>
            {/* BOS + Glicko */}
            <div>
              <div className="text-[10px] text-[var(--text)] opacity-50 uppercase tracking-wider mb-1">BOS · Glicko-2</div>
              <Row label="BOS SI"
                   value={adv?.bos_si != null ? adv.bos_si.toFixed(0) : null}
                   tone={adv?.bos_passed ? 'good' : adv?.bos_si != null ? 'bad' : undefined} />
              <Row label="Stable"
                   value={adv?.bos_passed != null ? (adv.bos_passed ? 'Yes' : 'No') : null}
                   tone={adv?.bos_passed ? 'good' : adv?.bos_passed === false ? 'bad' : undefined} />
              <Row label="Rating diff"  value={glickoDisplay} tone={glickoTone} />
            </div>
            {/* BREA/FHGI */}
            <div>
              <div className="text-[10px] text-[var(--text)] opacity-50 uppercase tracking-wider mb-1">BREA · FHGI</div>
              {adv?.brea_ri1 != null && (
                <Row label="BREA RI₁"   value={breaRiDisplay}    tone={breaRiTone} />
              )}
              {adv?.brea_fss != null && (
                <Row label="BREA FSS"   value={fmtNum(adv.brea_fss)} />
              )}
              {adv?.fhgi_gpi != null && (
                <Row label="FHGI GPI"   value={fmtNum(adv.fhgi_gpi, 3)} />
              )}
              {adv?.fhgi_fhgmi != null && (
                <Row label="FHGMI"      value={fmtNum(adv.fhgi_fhgmi)} />
              )}
              {adv?.fhgi_p_model != null && (
                <Row label="FHGI P(FH)" value={fhgiPDisplay} tone={fhgiPTone} />
              )}
              {adv?.wtcpm_ccs != null && (
                <Row label="WTCPM CCS"
                     value={adv.wtcpm_ccs.toFixed(0)}
                     tone={adv.wtcpm_ccs >= 80 ? 'good' : adv.wtcpm_ccs >= 65 ? 'warn' : 'bad'} />
              )}
              {adv?.wtcpm_di != null && (
                <Row label="DI" value={fmtNum(adv.wtcpm_di)} />
              )}
              {adv?.wtcpm_p_corners != null && (
                <Row label="P(corners≥2)" value={fmtPct(adv.wtcpm_p_corners)}
                     tone={adv.wtcpm_p_corners > 0.75 ? 'good' : 'warn'} />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
