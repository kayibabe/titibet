import { useState, useEffect, useRef } from 'react'
import {
  Sparkles, AlertTriangle, CheckCircle, MinusCircle, Loader2, RefreshCw, ArrowRight,
  Download, FileText, Printer, Zap, Clock, Bot,
} from 'lucide-react'
import { fetchAdvisorInsights, explainPicks } from '../../api/advisor'
import { fetchSignals } from '../../api/signals'
import { fetchBets } from '../../api/tracker'
import ADVISORS_META from './advisorsMeta'

// ── Report export helpers ─────────────────────────────────────────────────────

// Escape LLM/API-sourced strings before interpolating into raw report HTML —
// team names come from an external feed and advisor text from LLMs, so the
// export window must not render them as markup.
function esc(v) {
  return String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function buildReportHtml(data, date) {
  const generatedAt = new Date().toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
  })
  const reportDate = date
    ? new Date(date).toLocaleDateString('en-GB', { day: '2-digit', month: 'long', year: 'numeric' })
    : new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'long', year: 'numeric' })

  const verdictColor = { Strong: '#16a34a', Mixed: '#d97706', Caution: '#dc2626' }
  const verdictBg   = { Strong: '#f0fdf4', Mixed: '#fffbeb', Caution: '#fef2f2' }

  function picksHtml(picks) {
    if (!picks?.length) return ''
    return picks.map(pick => {
      const match = typeof pick === 'string'
        ? pick
        : (pick.home_team && pick.away_team ? `${pick.home_team} vs ${pick.away_team}` : pick.match_name || '—')
      const market = typeof pick === 'string' ? '' : (pick.market || '')
      const reason = typeof pick === 'string' ? '' : (pick.reason || '')
      return `
        <div style="margin:6px 0;padding:10px 12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span style="color:#16a34a;font-size:13px;">✓</span>
            <strong style="font-size:13px;color:#111827;">${esc(match)}</strong>
            ${market ? `<span style="font-size:11px;padding:2px 7px;background:#ede9fe;color:#7c3aed;border-radius:4px;font-weight:600;">${esc(market)}</span>` : ''}
          </div>
          ${reason ? `<p style="margin:5px 0 0 21px;font-size:12px;color:#6b7280;line-height:1.5;">${esc(reason)}</p>` : ''}
        </div>`
    }).join('')
  }

  function warningsHtml(warnings) {
    if (!warnings?.length) return ''
    return warnings.map(w => `
      <div style="display:flex;gap:8px;margin:5px 0;align-items:flex-start;">
        <span style="color:#d97706;font-size:13px;flex-shrink:0;">⚠</span>
        <span style="font-size:12px;color:#92400e;line-height:1.5;">${esc(w)}</span>
      </div>`).join('')
  }

  const advisorSections = (data.advisors || []).map(adv => {
    const verdict  = adv.result?.verdict  || 'Mixed'
    const picks    = adv.result?.top_picks || []
    const warnings = adv.result?.warnings  || []
    const summary  = adv.result?.summary   || ''
    const hasError = !!adv.result?.error

    return `
      <div style="margin-bottom:28px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;page-break-inside:avoid;">
        <!-- Advisor header -->
        <div style="padding:14px 18px;background:#f9fafb;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
          <div>
            <div style="display:flex;align-items:center;gap:8px;">
              <span style="font-size:18px;">${esc(adv.emoji || '')}</span>
              <strong style="font-size:15px;color:#111827;">${esc(adv.name)}</strong>
              <span style="font-size:11px;font-weight:700;padding:3px 9px;border-radius:20px;background:${verdictBg[verdict] || '#f9fafb'};color:${verdictColor[verdict] || '#374151'};border:1px solid ${verdictColor[verdict] || '#d1d5db'};">${esc(verdict)}</span>
            </div>
            <p style="margin:3px 0 0 26px;font-size:11px;color:#9ca3af;">${esc(adv.role || '')}</p>
          </div>
          <span style="font-size:10px;font-family:monospace;color:#7c3aed;">${esc(adv.model || '')}</span>
        </div>
        <!-- Advisor body -->
        <div style="padding:16px 18px;">
          ${hasError ? `<p style="color:#dc2626;font-size:12px;">⚠ Advisor request failed: ${esc(summary || adv.result?.error)}</p>` : ''}
          ${picks.length ? `<p style="font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#374151;margin:0 0 8px;">Top Picks</p>${picksHtml(picks)}` : ''}
          ${warnings.length ? `<p style="font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#374151;margin:${picks.length ? '14px' : '0'} 0 8px;">Watch Out</p>${warningsHtml(warnings)}` : ''}
          ${summary && !hasError ? `<p style="font-size:12px;color:#6b7280;line-height:1.6;margin:${picks.length || warnings.length ? '14px' : '0'} 0 0;padding-top:${picks.length || warnings.length ? '12px' : '0'};border-top:${picks.length || warnings.length ? '1px solid #f3f4f6' : 'none'};">${esc(summary)}</p>` : ''}
        </div>
      </div>`
  }).join('')

  const consensus = data.consensus_verdict || ''
  const matchCount = data.matches_analysed || 0

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>TiTiBet AI Advisory Report — ${reportDate}</title>
  <style>
    @media print {
      body { margin: 0; }
      .no-print { display: none !important; }
      @page { margin: 18mm 15mm; }
    }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111827; margin: 0; padding: 0; background: #fff; }
    .page { max-width: 820px; margin: 0 auto; padding: 32px 28px; }
  </style>
</head>
<body>
<div class="page">
  <!-- Masthead -->
  <div style="border-bottom:2px solid #7c3aed;padding-bottom:16px;margin-bottom:24px;">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:12px;">
      <div>
        <h1 style="margin:0;font-size:22px;color:#7c3aed;letter-spacing:-.3px;">TiTiBet</h1>
        <p style="margin:2px 0 0;font-size:13px;color:#6b7280;">AI Advisory Report</p>
      </div>
      <div style="text-align:right;font-size:11px;color:#9ca3af;line-height:1.7;">
        <div><strong style="color:#374151;">Date:</strong> ${reportDate}</div>
        <div><strong style="color:#374151;">Generated:</strong> ${generatedAt}</div>
        ${matchCount ? `<div><strong style="color:#374151;">Signals analysed:</strong> ${matchCount}</div>` : ''}
      </div>
    </div>
    ${consensus ? `
    <div style="margin-top:12px;display:inline-flex;align-items:center;gap:8px;padding:6px 14px;background:${verdictBg[consensus] || '#f9fafb'};border:1px solid ${verdictColor[consensus] || '#d1d5db'};border-radius:20px;">
      <span style="font-size:12px;color:#6b7280;font-weight:600;">Consensus:</span>
      <strong style="font-size:13px;color:${verdictColor[consensus] || '#374151'};">${esc(consensus)}</strong>
    </div>` : ''}
  </div>

  <!-- Advisor sections -->
  ${advisorSections}

  <!-- Disclaimer -->
  <div style="margin-top:28px;padding:12px 16px;background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;font-size:11px;color:#9ca3af;line-height:1.6;">
    <strong style="color:#6b7280;">Disclaimer:</strong> AI analysis is advisory only — always apply your own judgement and conduct your own research before staking.
    This report was generated by TiTiBet's AI Advisory Council (Scout · Strategist · Skeptic).
    Models used: ${esc((data.advisors || []).map(a => a.model).filter(Boolean).join(' · ') || 'multiple AI providers')}.
  </div>

  <!-- Print button (hidden on print) -->
  <div class="no-print" style="margin-top:20px;display:flex;gap:10px;justify-content:flex-end;">
    <button onclick="window.print()" style="padding:9px 18px;background:#7c3aed;color:#fff;border:none;border-radius:7px;font-size:13px;font-weight:600;cursor:pointer;">🖨 Print / Save as PDF</button>
    <button onclick="window.close()" style="padding:9px 18px;background:#f3f4f6;color:#374151;border:1px solid #d1d5db;border-radius:7px;font-size:13px;font-weight:600;cursor:pointer;">Close</button>
  </div>
</div>
</body>
</html>`
}

function exportPdf(data, date) {
  const html = buildReportHtml(data, date)
  const w = window.open('', '_blank')
  if (!w) { alert('Pop-up blocked — allow pop-ups for this site and try again.'); return }
  w.document.open()
  w.document.write(html)
  w.document.close()
  // Give styles a moment to paint, then trigger print
  setTimeout(() => { try { w.focus(); w.print() } catch (_) {} }, 400)
}

function exportWord(data, date) {
  const html = buildReportHtml(data, date)
  // Wrap in Word-compatible XML container
  const wordHtml = `<html xmlns:o='urn:schemas-microsoft-com:office:office'
    xmlns:w='urn:schemas-microsoft-com:office:word'
    xmlns='http://www.w3.org/TR/REC-html40'>${html.replace('</head>', `
    <xml><w:WordDocument><w:View>Print</w:View><w:Zoom>90</w:Zoom></w:WordDocument></xml>
  </head>`)}</html>`
  const blob = new Blob(['﻿', wordHtml], { type: 'application/msword' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  const slug = date ? date.replace(/-/g, '') : new Date().toISOString().slice(0,10).replace(/-/g, '')
  a.href     = url
  a.download = `titibet-advisory-${slug}.doc`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

// ── Export dropdown button ────────────────────────────────────────────────────
function ExportButton({ data, date }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    function handleClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-xs text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] transition-colors"
        title="Download advisory report"
      >
        <Download size={11} />
        Export
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 w-44 rounded-lg border border-[var(--border)] bg-[var(--bg)] shadow-lg overflow-hidden">
          <button
            onClick={() => { exportPdf(data, date); setOpen(false) }}
            className="flex w-full items-center gap-2 px-3 py-2.5 text-xs text-[var(--text)] hover:bg-[var(--code-bg)] hover:text-[var(--text-h)] transition-colors"
          >
            <Printer size={12} className="text-[var(--accent)]" />
            <span>Save as PDF</span>
          </button>
          <div className="border-t border-[var(--border)]" />
          <button
            onClick={() => { exportWord(data, date); setOpen(false) }}
            className="flex w-full items-center gap-2 px-3 py-2.5 text-xs text-[var(--text)] hover:bg-[var(--code-bg)] hover:text-[var(--text-h)] transition-colors"
          >
            <FileText size={12} className="text-[var(--accent)]" />
            <span>Export Word (.doc)</span>
          </button>
        </div>
      )}
    </div>
  )
}

// ── Accumulator ticket ────────────────────────────────────────────────────────

function fmtLegKickoff(iso) {
  if (!iso) return null
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

const LEG_RESULT_CFG = {
  won:     { icon: CheckCircle,   cls: 'text-green-400',  label: 'Won'  },
  lost:    { icon: AlertTriangle, cls: 'text-red-400',    label: 'Lost' },
  void:    { icon: MinusCircle,   cls: 'text-[var(--text)] opacity-60', label: 'Void' },
  pending: { icon: Clock,         cls: 'text-[var(--text)] opacity-50', label: null },
}

function LegResultBadge({ result, score }) {
  const cfg = LEG_RESULT_CFG[result] || LEG_RESULT_CFG.pending
  const Icon = cfg.icon
  if (result === 'pending' || !result) {
    return score ? <span className={`text-[10px] font-mono ${cfg.cls}`}>{score}</span> : null
  }
  return (
    <span className={`flex items-center gap-1 text-[10px] font-bold ${cfg.cls}`}>
      <Icon size={11} />
      {cfg.label}{score ? ` ${score}` : ''}
    </span>
  )
}

// ── Verdict badge ─────────────────────────────────────────────────────────────
function VerdictBadge({ verdict }) {
  const cfg = {
    Strong:  { cls: 'bg-green-500/15 text-green-400 border-green-500/30',    icon: CheckCircle,   label: 'Strong'  },
    Mixed:   { cls: 'bg-amber-500/20 text-amber-500 border-amber-500/40',    icon: MinusCircle,   label: 'Mixed'   },
    Caution: { cls: 'bg-red-500/15 text-red-400 border-red-500/30',          icon: AlertTriangle, label: 'Caution' },
  }[verdict] || { cls: 'bg-[var(--code-bg)] text-[var(--text)] border-[var(--border)]', icon: MinusCircle, label: verdict || '—' }

  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full border ${cfg.cls}`}>
      <Icon size={11} />
      {cfg.label}
    </span>
  )
}

// ── Single advisor card ───────────────────────────────────────────────────────
function AdvisorCard({ advisor, onFilterPick }) {
  const { name, role, model, emoji, result } = advisor
  const verdict  = result?.verdict   || 'Mixed'
  const topPicks = result?.top_picks || []
  const warnings = result?.warnings  || []
  const summary  = result?.summary   || ''
  const hasError = !!result?.error

  function renderPick(pick, i) {
    // Backward compat: old string format from cached/pre-DE1 responses
    if (typeof pick === 'string') {
      return (
        <li key={i} className="flex items-start gap-1.5 text-xs text-[var(--text-h)] leading-snug">
          <CheckCircle size={11} className="text-green-400 shrink-0 mt-0.5" />
          {pick}
        </li>
      )
    }

    // New structured object format: { home_team, away_team, market, reason }
    const matchName = pick.home_team && pick.away_team
      ? `${pick.home_team} vs ${pick.away_team}`
      : pick.match_name || '—'

    return (
      <li key={i} className="flex flex-col gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--code-bg)] px-3 py-2.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <CheckCircle size={11} className="text-green-400 shrink-0" />
          <span className="text-xs font-semibold text-[var(--text-h)] flex-1 min-w-0">{matchName}</span>
          {pick.market && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-[var(--accent)]/15 text-[var(--accent)] font-semibold shrink-0">
              {pick.market}
            </span>
          )}
        </div>
        {pick.reason && (
          <p className="text-[11px] text-[var(--text)] opacity-80 leading-snug">{pick.reason}</p>
        )}
        {onFilterPick && pick.market && (
          <button
            onClick={() => onFilterPick(pick)}
            className="self-start flex items-center gap-0.5 text-[10px] text-[var(--accent)] hover:underline font-medium"
          >
            Filter signals <ArrowRight size={10} />
          </button>
        )}
      </li>
    )
  }

  return (
    <div className="flex flex-col rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[var(--border)] bg-[var(--code-bg)] flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-base leading-none">{emoji}</span>
            <span className="text-sm font-bold text-[var(--text-h)]">{name}</span>
            <VerdictBadge verdict={verdict} />
          </div>
          <p className="text-[11px] text-[var(--text)] opacity-65 mt-0.5">{role}</p>
        </div>
        <span className="text-[10px] font-mono text-[var(--accent)] opacity-85 shrink-0 pt-0.5">{model}</span>
      </div>

      {/* Body */}
      <div className="px-4 py-3 flex-1 space-y-3">

        {hasError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2">
            <p className="text-[11px] font-semibold text-red-500">Advisor request failed</p>
            <p className="mt-0.5 text-xs text-red-500/90">{summary || result.error}</p>
          </div>
        )}

        {topPicks.length > 0 && (
          <div>
            <p className="text-[11px] font-bold uppercase tracking-widest text-[var(--text-h)] mb-1.5">
              Top Picks
            </p>
            <ul className="space-y-1.5">
              {topPicks.map((pick, i) => renderPick(pick, i))}
            </ul>
          </div>
        )}

        {warnings.length > 0 && (
          <div>
            <p className="text-[11px] font-bold uppercase tracking-widest text-[var(--text-h)] mb-1.5">
              Watch Out
            </p>
            <ul className="space-y-1.5">
              {warnings.map((w, i) => (
                <li key={i} className="flex items-start gap-1.5 text-xs text-amber-500 leading-snug font-medium">
                  <AlertTriangle size={11} className="shrink-0 mt-0.5" />
                  {w}
                </li>
              ))}
            </ul>
          </div>
        )}

        {summary && !hasError && (
          <p className="text-xs text-[var(--text)] opacity-85 leading-relaxed border-t border-[var(--border)] pt-3">
            {summary}
          </p>
        )}
      </div>
    </div>
  )
}

// ── Loading skeleton ──────────────────────────────────────────────────────────
function AdvisorSkeleton({ emoji, name, role }) {
  return (
    <div className="flex flex-col rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden animate-pulse">
      <div className="px-4 py-3 border-b border-[var(--border)] bg-[var(--code-bg)]">
        <div className="flex items-center gap-2">
          <span className="text-base leading-none">{emoji}</span>
          <span className="text-sm font-bold text-[var(--text-h)]">{name}</span>
          <span className="text-[10px] text-[var(--accent)] opacity-75">Thinking…</span>
        </div>
        <p className="text-[11px] text-[var(--text)] opacity-65 mt-0.5">{role}</p>
      </div>
      <div className="px-4 py-4 space-y-2.5">
        {[65, 85, 50, 75, 60].map((w, i) => (
          <div key={i} className="h-2 rounded-full bg-[var(--border)]" style={{ width: `${w}%` }} />
        ))}
      </div>
    </div>
  )
}

// ── Not configured ────────────────────────────────────────────────────────────
function NotConfigured({ message }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-6 py-10 text-center">
      <div className="text-4xl mb-3">🔑</div>
      <p className="text-sm font-semibold text-[var(--text-h)] mb-1">AI Advisors not configured</p>
      <p className="text-xs text-[var(--text)] opacity-75 mb-4">Configure at least one provider key in backend/.env — several are free</p>
      <pre className="text-xs text-[var(--text)] opacity-85 whitespace-pre-wrap text-left inline-block bg-[var(--bg)] rounded-lg px-4 py-3 border border-[var(--border)]">
        {message}
      </pre>
    </div>
  )
}

// ── Signal summary table ──────────────────────────────────────────────────────
const _norm = s => (s || '').toLowerCase().trim()

function SignalSummaryTable({ signals, advisors }) {
  if (!signals.length || !advisors?.length) return null

  const confStyle = c =>
    c === 'High'   ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' :
    c === 'Medium' ? 'text-amber-400   bg-amber-500/10   border-amber-500/30'   :
                     'text-rose-400    bg-rose-500/10    border-rose-500/30'

  const rows = signals.map(sig => {
    const advisorStatus = advisors.map(adv => ({
      id:     adv.id,
      emoji:  adv.emoji,
      name:   adv.name,
      picked: (adv.result?.top_picks || []).some(p =>
        _norm(p.home_team) === _norm(sig.home_team) &&
        _norm(p.away_team) === _norm(sig.away_team) &&
        _norm(p.market)    === _norm(sig.market)
      ),
    }))
    const pickCount  = advisorStatus.filter(a => a.picked).length
    const primaryProb = Math.max(sig.bayesian?.prob ?? 0, sig.poisson?.prob ?? 0)
    const odds        = sig.bayesian?.best_odd ?? sig.bayesian_best_odd
    const ko = sig.kickoff_at
      ? new Date(sig.kickoff_at.endsWith('Z') || sig.kickoff_at.includes('+') ? sig.kickoff_at : sig.kickoff_at + 'Z')
          .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : null
    return { sig, advisorStatus, pickCount, primaryProb, odds, ko }
  })

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
      <table className="w-full text-xs text-[var(--text)] border-collapse">
        <thead>
          <tr className="border-b border-[var(--border)] bg-[var(--code-bg)]">
            <th className="px-3 py-2.5 text-left font-semibold text-[var(--text-h)] whitespace-nowrap">Match</th>
            <th className="px-3 py-2.5 text-left font-semibold text-[var(--text-h)] whitespace-nowrap">Market</th>
            <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)]">Conf</th>
            <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)]">Prob</th>
            <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)]">Odds</th>
            {advisors.map(adv => (
              <th key={adv.id} className="px-2 py-2.5 text-center hidden sm:table-cell" title={adv.name}>
                {adv.emoji}
              </th>
            ))}
            <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)] sm:hidden">AI</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {rows.map(({ sig, advisorStatus, pickCount, primaryProb, odds, ko }, i) => {
            const rowCls =
              pickCount === advisors.length ? 'bg-emerald-500/5 hover:bg-emerald-500/8' :
              pickCount >= 2               ? 'bg-amber-500/3  hover:bg-amber-500/6'    :
                                             'hover:bg-[var(--code-bg)]'
            return (
              <tr key={sig.id ?? i} className={`transition-colors ${rowCls}`}>
                <td className="px-3 py-2.5 min-w-[150px]">
                  <div className="font-medium text-[var(--text-h)] leading-tight">
                    {sig.home_team} <span className="opacity-40 font-normal">vs</span> {sig.away_team}
                  </div>
                  <div className="opacity-45 text-[10px] mt-0.5 leading-tight">
                    {sig.league}{ko ? ` · ${ko}` : ''}
                  </div>
                </td>
                <td className="px-3 py-2.5 whitespace-nowrap">
                  <span className="font-semibold text-[var(--accent)]">{sig.market}</span>
                </td>
                <td className="px-2 py-2.5 text-center">
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full border ${confStyle(sig.dual_confidence)}`}>
                    {sig.dual_confidence?.[0] ?? '?'}
                  </span>
                </td>
                <td className="px-2 py-2.5 text-center font-bold tabular-nums">
                  {primaryProb > 0 ? `${Math.round(primaryProb * 100)}%` : '—'}
                </td>
                <td className="px-2 py-2.5 text-center font-mono tabular-nums opacity-80">
                  {odds ? Number(odds).toFixed(2) : '—'}
                </td>
                {advisorStatus.map(adv => (
                  <td key={adv.id} className="px-2 py-2.5 text-center hidden sm:table-cell">
                    {adv.picked
                      ? <CheckCircle size={13} className="mx-auto text-emerald-400" />
                      : <span className="opacity-20 text-[10px]">—</span>
                    }
                  </td>
                ))}
                <td className="px-2 py-2.5 text-center sm:hidden">
                  <span className={`font-bold text-[11px] ${
                    pickCount === advisors.length ? 'text-emerald-400' :
                    pickCount >= 2               ? 'text-amber-400'   :
                    pickCount === 1              ? 'text-[var(--text)]' : 'opacity-25'
                  }`}>
                    {pickCount}/{advisors.length}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── System auto-tracked bets table ───────────────────────────────────────────
const _RESULT_CFG = {
  Won:     { cls: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30', label: 'Won'     },
  Lost:    { cls: 'text-red-400    bg-red-500/10     border-red-500/30',     label: 'Lost'    },
  Void:    { cls: 'text-slate-400  bg-slate-500/10   border-slate-500/30',   label: 'Void'    },
  Pending: { cls: 'text-amber-400  bg-amber-500/10   border-amber-500/30',   label: 'Pending' },
}

function SystemTrackedTable({ systemBets, signals, advisors, explanations, explanationsLoading }) {
  if (!systemBets.length) return null

  // Build lookup from fixture_id:market → signal for probability data
  const sigMap = new Map()
  for (const sig of signals) {
    sigMap.set(`${sig.fixture_id}:${sig.market}`, sig)
  }

  const confStyle = c =>
    c === 'High'   ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' :
    c === 'Medium' ? 'text-amber-400   bg-amber-500/10   border-amber-500/30'   :
                     'text-rose-400    bg-rose-500/10    border-rose-500/30'

  const rows = systemBets.map(bet => {
    const vsParts  = (bet.match_name || '').split(' vs ')
    const homeTeam = vsParts[0] ?? ''
    const awayTeam = vsParts.slice(1).join(' vs ') ?? ''

    const sig = sigMap.get(`${bet.fixture_id}:${bet.market_type}`)
    const primaryProb = sig ? Math.max(sig.bayesian?.prob ?? 0, sig.poisson?.prob ?? 0) : 0

    const advisorStatus = (advisors || []).map(adv => ({
      id:     adv.id,
      emoji:  adv.emoji,
      name:   adv.name,
      picked: (adv.result?.top_picks || []).some(p =>
        _norm(p.home_team) === _norm(homeTeam) &&
        _norm(p.away_team) === _norm(awayTeam) &&
        _norm(p.market)    === _norm(bet.market_type)
      ),
    }))

    const pickCount = advisorStatus.filter(a => a.picked).length
    const hasScore  = bet.home_score != null && bet.away_score != null
    const score     = hasScore ? `${bet.home_score}-${bet.away_score}` : null
    const result    = _RESULT_CFG[bet.result_status] ?? _RESULT_CFG.Pending

    return { bet, homeTeam, awayTeam, sig, primaryProb, advisorStatus, pickCount, score, result }
  })

  // Summary counts
  const won     = rows.filter(r => r.bet.result_status === 'Won').length
  const lost    = rows.filter(r => r.bet.result_status === 'Lost').length
  const pending = rows.filter(r => r.bet.result_status === 'Pending').length
  const settled = won + lost
  const hitRate = settled > 0 ? Math.round(won / settled * 100) : null

  return (
    <div className="space-y-2">
      {/* Summary strip */}
      <div className="flex items-center gap-3 px-1 text-xs text-[var(--text)]">
        <span className="flex items-center gap-1.5 font-semibold text-[var(--accent)]">
          <Zap size={11} />
          {rows.length} auto-tracked pick{rows.length !== 1 ? 's' : ''}
        </span>
        {settled > 0 && (
          <>
            <span className="opacity-40">·</span>
            <span>
              <span className="font-semibold text-emerald-400">{won}W</span>
              {' / '}
              <span className="font-semibold text-red-400">{lost}L</span>
              {hitRate !== null && (
                <span className="ml-1 font-semibold text-[var(--text-h)]">({hitRate}%)</span>
              )}
            </span>
          </>
        )}
        {pending > 0 && (
          <>
            <span className="opacity-40">·</span>
            <span className="opacity-60">{pending} pending</span>
          </>
        )}
      </div>

      <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
        <table className="w-full text-xs text-[var(--text)] border-collapse">
          <thead>
            <tr className="border-b border-[var(--border)] bg-[var(--code-bg)]">
              <th className="px-3 py-2.5 text-left font-semibold text-[var(--text-h)] whitespace-nowrap">Match</th>
              <th className="px-3 py-2.5 text-left font-semibold text-[var(--text-h)] whitespace-nowrap">Market</th>
              <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)]">Conf</th>
              <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)]">Prob</th>
              <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)]">Odds</th>
              <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)] whitespace-nowrap">Score</th>
              <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)]">Result</th>
              {(advisors || []).map(adv => (
                <th key={adv.id} className="px-2 py-2.5 text-center hidden sm:table-cell" title={adv.name}>
                  {adv.emoji}
                </th>
              ))}
              {(advisors || []).length > 0 && (
                <th className="px-2 py-2.5 text-center font-semibold text-[var(--text-h)] sm:hidden">AI</th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {rows.map(({ bet, homeTeam, awayTeam, primaryProb, advisorStatus, pickCount, score, result }, i) => {
              const rowBase =
                bet.result_status === 'Won'  ? 'bg-emerald-500/5 hover:bg-emerald-500/8' :
                bet.result_status === 'Lost' ? 'bg-red-500/5    hover:bg-red-500/8'     :
                                               'hover:bg-[var(--code-bg)]'
              const explanation = explanations?.[String(bet.id)]
              return (
                <>
                  <tr key={bet.id ?? i} className={`transition-colors ${rowBase}`}>
                    <td className="px-3 py-2.5 min-w-[150px]">
                      <div className="font-medium text-[var(--text-h)] leading-tight">
                        {homeTeam} <span className="opacity-40 font-normal">vs</span> {awayTeam}
                      </div>
                      {bet.league && (
                        <div className="opacity-45 text-[10px] mt-0.5 leading-tight">{bet.league}</div>
                      )}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      <span className="font-semibold text-[var(--accent)]">{bet.market_type}</span>
                    </td>
                    <td className="px-2 py-2.5 text-center">
                      {bet.dual_confidence ? (
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full border ${confStyle(bet.dual_confidence)}`}>
                          {bet.dual_confidence[0]}
                        </span>
                      ) : <span className="opacity-30">—</span>}
                    </td>
                    <td className="px-2 py-2.5 text-center font-bold tabular-nums">
                      {primaryProb > 0 ? `${Math.round(primaryProb * 100)}%` : '—'}
                    </td>
                    <td className="px-2 py-2.5 text-center font-mono tabular-nums opacity-80">
                      {bet.odds ? Number(bet.odds).toFixed(2) : '—'}
                    </td>
                    <td className="px-2 py-2.5 text-center font-mono font-bold tabular-nums">
                      {score ?? <span className="opacity-30">—</span>}
                    </td>
                    <td className="px-2 py-2.5 text-center">
                      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full border ${result.cls}`}>
                        {result.label}
                      </span>
                    </td>
                    {advisorStatus.map(adv => (
                      <td key={adv.id} className="px-2 py-2.5 text-center hidden sm:table-cell">
                        {adv.picked
                          ? <CheckCircle size={13} className="mx-auto text-emerald-400" />
                          : <span className="opacity-20 text-[10px]">—</span>
                        }
                      </td>
                    ))}
                    {advisorStatus.length > 0 && (
                      <td className="px-2 py-2.5 text-center sm:hidden">
                        <span className={`font-bold text-[11px] ${
                          pickCount === advisorStatus.length ? 'text-emerald-400' :
                          pickCount >= 2                    ? 'text-amber-400'   :
                          pickCount === 1                   ? 'text-[var(--text)]' : 'opacity-25'
                        }`}>
                          {pickCount}/{advisorStatus.length}
                        </span>
                      </td>
                    )}
                  </tr>
                  {/* AI explanation row */}
                  {(explanation || explanationsLoading) && (
                    <tr key={`${bet.id ?? i}-explain`} className={`border-t-0 ${rowBase}`}>
                      <td colSpan={100} className="px-3 pb-3 pt-0">
                        <div className="flex items-start gap-2 rounded-lg bg-[var(--accent)]/5 border border-[var(--accent)]/20 px-3 py-2.5 mt-1">
                          <Bot size={13} className="text-[var(--accent)] shrink-0 mt-1" />
                          {explanation ? (
                            <p className="text-xs leading-relaxed text-[var(--text)] opacity-90">
                              {explanation.split(/(\*\*[^*]+\*\*)/).map((part, j) =>
                                part.startsWith('**') && part.endsWith('**')
                                  ? <strong key={j} className="text-[var(--text-h)]">{part.slice(2, -2)}</strong>
                                  : part
                              )}
                            </p>
                          ) : (
                            <p className="text-xs text-[var(--text)] opacity-40 animate-pulse">Generating analysis…</p>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────
/**
 * AIAdvisorPanel
 *
 * tabMode=false (default) — collapsible panel with header bar (used inline at page bottom)
 * tabMode=true            — always expanded, auto-runs on mount, no collapse toggle
 */
export default function AIAdvisorPanel({ date, tabMode = false, onFilterPick }) {
  const [loading,  setLoading]  = useState(false)
  const [data,     setData]     = useState(null)
  const [error,    setError]    = useState(null)
  const [lastDate, setLastDate] = useState(null)
  // Must be declared unconditionally — only used in panel mode
  const [open,     setOpen]     = useState(false)
  const [signals,    setSignals]    = useState([])
  const [systemBets, setSystemBets] = useState([])
  const [explanations,        setExplanations]        = useState({})
  const [explanationsLoading, setExplanationsLoading] = useState(false)

  // In tab mode auto-run when the panel mounts or the date changes
  useEffect(() => {
    if (tabMode && (!data || lastDate !== date)) {
      runAnalysis()
    }
  }, [tabMode, date]) // eslint-disable-line

  // force=true bypasses both the local guard and the server's daily cache —
  // used by the Refresh button so it actually re-runs the AI pipeline.
  async function runAnalysis(force = false) {
    if (loading) return
    if (!force && data && lastDate === date) return

    setLoading(true)
    setError(null)
    setExplanations({})
    try {
      const _SYS_KEYS = new Set(['system_auto', 'system_dual'])
      const [result, signalData, betData] = await Promise.all([
        fetchAdvisorInsights(date, { force }),
        fetchSignals({ date, best_per_fixture: false }).catch(() => []),
        fetchBets({ date_from: date, date_to: date }).catch(() => []),
      ])
      setData(result)
      const sigs = Array.isArray(signalData) ? signalData : (signalData?.signals ?? [])
      setSignals(sigs)
      const bets = Array.isArray(betData) ? betData : []
      const sysBets = bets.filter(b => _SYS_KEYS.has(b.source_rule_key))
      setSystemBets(sysBets)
      setLastDate(date)

      // Fire explain-picks in background — one LLM call for all picks
      if (sysBets.length > 0) {
        setExplanationsLoading(true)
        const sigMap = new Map()
        for (const sig of sigs) sigMap.set(`${sig.fixture_id}:${sig.market}`, sig)
        explainPicks(sysBets.map(bet => {
          const sig  = sigMap.get(`${bet.fixture_id}:${bet.market_type}`)
          const prob = sig ? Math.max(sig.bayesian?.prob ?? 0, sig.poisson?.prob ?? 0) : 0
          return {
            key:    String(bet.id),
            match:  bet.match_name,
            market: bet.market_type,
            prob:   prob > 0 ? `${Math.round(prob * 100)}%` : null,
            odds:   bet.odds ? Number(bet.odds).toFixed(2) : null,
            result: bet.result_status,
            score:  bet.home_score != null && bet.away_score != null
                      ? `${bet.home_score}-${bet.away_score}` : null,
          }
        }))
          .then(d => setExplanations(d.explanations || {}))
          .catch(() => {})
          .finally(() => setExplanationsLoading(false))
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const isConfigured = data?.configured !== false

  // ── Shared inner content ──────────────────────────────────────────────────
  function PanelContent() {
    return (
      <div className="space-y-4">
        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3">
            <p className="text-sm font-semibold text-red-400">Advisor unavailable</p>
            <p className="mt-0.5 text-xs text-red-400/90">{error}</p>
          </div>
        )}

        {!data && !loading && !error && (
          <p className="text-sm text-[var(--text)] opacity-65 text-center py-10">
            Click <strong className="text-[var(--accent)]">Get Analysis</strong> to run the advisory council.
          </p>
        )}

        {data && !isConfigured && <NotConfigured message={data.message} />}

        {data && isConfigured && !data.advisors?.length && (
          <p className="text-sm text-[var(--text)] opacity-75 text-center py-10">{data.message}</p>
        )}

        {/* Signal overview table — shown once both signals and advisor data are loaded */}
        {!loading && signals.length > 0 && data?.advisors?.length > 0 && (
          <>
            <div className="flex items-center gap-3">
              <div className="flex-1 h-px bg-[var(--border)]" />
              <span className="text-[10px] font-bold text-[var(--text)] opacity-50 tracking-widest uppercase">Signal Overview</span>
              <div className="flex-1 h-px bg-[var(--border)]" />
            </div>
            <SignalSummaryTable signals={signals} advisors={data.advisors} />
          </>
        )}

        {/* System auto-tracked picks table */}
        {!loading && systemBets.length > 0 && (
          <>
            <div className="flex items-center gap-3">
              <div className="flex-1 h-px bg-[var(--border)]" />
              <span className="text-[10px] font-bold text-[var(--text)] opacity-50 tracking-widest uppercase">System Auto-Tracked</span>
              <div className="flex-1 h-px bg-[var(--border)]" />
            </div>
            <SystemTrackedTable
              systemBets={systemBets}
              signals={signals}
              advisors={data?.advisors}
              explanations={explanations}
              explanationsLoading={explanationsLoading}
            />
          </>
        )}

        {(loading || data?.advisors?.length > 0) && (
          <>
            <div className="flex items-center gap-3 pt-1">
              <div className="flex-1 h-px bg-[var(--border)]" />
              <span className="text-[10px] font-bold text-[var(--text)] opacity-50 tracking-widest uppercase">Advisory Council</span>
              <div className="flex-1 h-px bg-[var(--border)]" />
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              {loading
                ? ADVISORS_META.map(m => <AdvisorSkeleton key={m.id} {...m} />)
                : data.advisors.map(adv => <AdvisorCard key={adv.id} advisor={adv} onFilterPick={onFilterPick} />)
              }
            </div>
          </>
        )}

        {!loading && data?.advisors?.length > 0 && (
          <p className="text-[10px] text-[var(--text)] opacity-55 text-center pt-1">
            AI analysis is advisory only — always apply your own judgement before staking. ·{' '}
            {data.advisors.map(a => a.model).join(' · ')}
          </p>
        )}
      </div>
    )
  }

  // ── Tab mode: no chrome, auto-runs, refresh button in corner ─────────────
  if (tabMode) {
    return (
      <div className="space-y-4">
        {/* Tab-mode header */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3 flex-wrap">
            <p className="text-xs text-[var(--text-h)] font-medium">
              {loading
                ? 'Consulting 3 AI models simultaneously…'
                : data?.matches_analysed
                  ? `${data.matches_analysed} signals analysed · Scout · Strategist · Skeptic`
                  : 'Scout · Strategist · Skeptic'
              }
            </p>
            {/* Consensus verdict badge — shown once all advisors have responded */}
            {!loading && data?.consensus_verdict && (
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-[var(--text)] opacity-60 font-medium">Consensus:</span>
                <VerdictBadge verdict={data.consensus_verdict} />
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            {!loading && data?.advisors?.length > 0 && (
              <ExportButton data={data} date={date} />
            )}
            <button
              onClick={() => runAnalysis(true)}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-xs text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)] disabled:opacity-40 transition-colors"
            >
              {loading
                ? <><Loader2 size={11} className="animate-spin" /> Analysing…</>
                : <><RefreshCw size={11} /> Refresh</>
              }
            </button>
          </div>
        </div>
        <PanelContent />
      </div>
    )
  }

  // ── Default (panel) mode: collapsible with header ─────────────────────────
  function toggleOpen() {
    if (!open) {
      setOpen(true)
      if (!data || lastDate !== date) runAnalysis()
    } else {
      setOpen(false)
    }
  }

  return (
    <div className="rounded-xl border border-[var(--accent-border)] bg-[var(--bg)] overflow-hidden">
      {/* Header bar */}
      <div
        className="flex items-center justify-between px-5 py-3.5 cursor-pointer hover:bg-[var(--code-bg)] transition-colors select-none"
        onClick={toggleOpen}
      >
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[var(--accent-bg)] border border-[var(--accent-border)] flex items-center justify-center shrink-0">
            <Sparkles size={15} className="text-[var(--accent)]" />
          </div>
          <div>
            <p className="text-sm font-bold text-[var(--text-h)]">AI Advisory Council</p>
            <p className="text-[11px] text-[var(--text-h)] font-medium">
              Scout · Strategist · Skeptic
              {data?.matches_analysed ? ` · ${data.matches_analysed} signals analysed` : ' · 3 models'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Consensus verdict — visible in collapsed header once analysis is done */}
          {!loading && data?.consensus_verdict && lastDate === date && (
            <VerdictBadge verdict={data.consensus_verdict} />
          )}
          {!loading && data?.advisors?.length > 0 && lastDate === date && (
            <div onClick={e => e.stopPropagation()}>
              <ExportButton data={data} date={date} />
            </div>
          )}
          <button
            onClick={e => { e.stopPropagation(); runAnalysis(Boolean(data && lastDate === date)) }}
            disabled={loading}
            className={`
              flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all
              ${loading
                ? 'bg-[var(--accent-bg)] text-[var(--accent)] opacity-85 cursor-not-allowed'
                : 'bg-[var(--accent)] text-white hover:opacity-90 active:scale-95'
              }
            `}
          >
            {loading
              ? <><Loader2 size={11} className="animate-spin" /> Analysing…</>
              : <><Sparkles size={11} /> {data && lastDate === date ? 'Refresh' : 'Get Analysis'}</>
            }
          </button>
        </div>
      </div>

      {open && (
        <div className="border-t border-[var(--border)] p-5">
          <PanelContent />
        </div>
      )}
    </div>
  )
}
