import { useState, useEffect, useRef } from 'react'
import {
  Sparkles, AlertTriangle, CheckCircle, MinusCircle, Loader2, RefreshCw, ArrowRight,
  Download, FileText, Printer, Ticket, Zap,
} from 'lucide-react'
import { fetchAdvisorInsights } from '../../api/advisor'
import ADVISORS_META from './advisorsMeta'

// ── Report export helpers ─────────────────────────────────────────────────────

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
            <strong style="font-size:13px;color:#111827;">${match}</strong>
            ${market ? `<span style="font-size:11px;padding:2px 7px;background:#ede9fe;color:#7c3aed;border-radius:4px;font-weight:600;">${market}</span>` : ''}
          </div>
          ${reason ? `<p style="margin:5px 0 0 21px;font-size:12px;color:#6b7280;line-height:1.5;">${reason}</p>` : ''}
        </div>`
    }).join('')
  }

  function warningsHtml(warnings) {
    if (!warnings?.length) return ''
    return warnings.map(w => `
      <div style="display:flex;gap:8px;margin:5px 0;align-items:flex-start;">
        <span style="color:#d97706;font-size:13px;flex-shrink:0;">⚠</span>
        <span style="font-size:12px;color:#92400e;line-height:1.5;">${w}</span>
      </div>`).join('')
  }

  const acca = data.accumulator
  const accaSection = acca && acca.legs?.length ? `
    <div style="margin-bottom:28px;border:2px solid #7c3aed;border-radius:10px;overflow:hidden;page-break-inside:avoid;">
      <div style="padding:12px 18px;background:#ede9fe;border-bottom:1px solid #ddd6fe;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
        <div style="display:flex;align-items:center;gap:8px;">
          <span style="font-size:16px;">🎟️</span>
          <strong style="font-size:15px;color:#5b21b6;">Acca of the Day</strong>
          ${acca.confidence ? `<span style="font-size:11px;font-weight:700;padding:2px 8px;border-radius:12px;background:#fff;color:#7c3aed;border:1px solid #c4b5fd;">${acca.confidence}</span>` : ''}
        </div>
        ${acca.combined_odds ? `<span style="font-size:18px;font-weight:800;color:#7c3aed;">@ ${acca.combined_odds}</span>` : ''}
      </div>
      <div style="padding:14px 18px;space-y:8px;">
        ${(acca.legs || []).map((leg, i) => {
          const match = leg.home_team && leg.away_team ? `${leg.home_team} vs ${leg.away_team}` : '—'
          return `<div style="display:flex;align-items:flex-start;gap:10px;margin:8px 0;padding:10px 12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;">
            <span style="min-width:22px;height:22px;background:#ede9fe;color:#7c3aed;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0;">${i+1}</span>
            <div style="flex:1;">
              <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                <strong style="font-size:13px;color:#111827;">${match}</strong>
                ${leg.market ? `<span style="font-size:11px;padding:2px 7px;background:#ede9fe;color:#7c3aed;border-radius:4px;font-weight:600;">${leg.market}</span>` : ''}
              </div>
              ${leg.reason ? `<p style="margin:4px 0 0;font-size:12px;color:#6b7280;line-height:1.5;">${leg.reason}</p>` : ''}
            </div>
            ${leg.odd != null ? `<span style="font-weight:700;font-size:13px;color:#111827;flex-shrink:0;">${Number(leg.odd).toFixed(2)}</span>` : ''}
          </div>`
        }).join('')}
        ${acca.combined_odds ? `<div style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #e5e7eb;margin-top:4px;font-size:12px;"><span style="color:#6b7280;">${acca.legs.length} legs combined</span><strong style="color:#7c3aed;">Combined odds: ${acca.combined_odds}</strong></div>` : ''}
        ${acca.rationale ? `<p style="font-size:12px;color:#6b7280;line-height:1.6;margin-top:8px;padding-top:8px;border-top:1px solid #f3f4f6;">${acca.rationale}</p>` : ''}
      </div>
    </div>` : ''

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
              <span style="font-size:18px;">${adv.emoji || ''}</span>
              <strong style="font-size:15px;color:#111827;">${adv.name}</strong>
              <span style="font-size:11px;font-weight:700;padding:3px 9px;border-radius:20px;background:${verdictBg[verdict] || '#f9fafb'};color:${verdictColor[verdict] || '#374151'};border:1px solid ${verdictColor[verdict] || '#d1d5db'};">${verdict}</span>
            </div>
            <p style="margin:3px 0 0 26px;font-size:11px;color:#9ca3af;">${adv.role || ''}</p>
          </div>
          <span style="font-size:10px;font-family:monospace;color:#7c3aed;">${adv.model || ''}</span>
        </div>
        <!-- Advisor body -->
        <div style="padding:16px 18px;">
          ${hasError ? `<p style="color:#dc2626;font-size:12px;">⚠ Advisor request failed: ${summary || adv.result?.error}</p>` : ''}
          ${picks.length ? `<p style="font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#374151;margin:0 0 8px;">Top Picks</p>${picksHtml(picks)}` : ''}
          ${warnings.length ? `<p style="font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#374151;margin:${picks.length ? '14px' : '0'} 0 8px;">Watch Out</p>${warningsHtml(warnings)}` : ''}
          ${summary && !hasError ? `<p style="font-size:12px;color:#6b7280;line-height:1.6;margin:${picks.length || warnings.length ? '14px' : '0'} 0 0;padding-top:${picks.length || warnings.length ? '12px' : '0'};border-top:${picks.length || warnings.length ? '1px solid #f3f4f6' : 'none'};">${summary}</p>` : ''}
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
      <strong style="font-size:13px;color:${verdictColor[consensus] || '#374151'};">${consensus}</strong>
    </div>` : ''}
  </div>

  <!-- Accumulator ticket -->
  ${accaSection}

  <!-- Advisor sections -->
  ${advisorSections}

  <!-- Disclaimer -->
  <div style="margin-top:28px;padding:12px 16px;background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;font-size:11px;color:#9ca3af;line-height:1.6;">
    <strong style="color:#6b7280;">Disclaimer:</strong> AI analysis is advisory only — always apply your own judgement and conduct your own research before staking.
    This report was generated by TiTiBet's AI Advisory Council (Scout · Strategist · Skeptic).
    Models used: ${(data.advisors || []).map(a => a.model).filter(Boolean).join(' · ') || 'multiple AI providers'}.
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
function AccaTicket({ acca }) {
  if (!acca) return null
  const { legs = [], combined_odds, rationale, confidence, error, tracked } = acca

  const confCfg = {
    High:   { cls: 'text-green-400 border-green-500/40 bg-green-500/10',  dot: 'bg-green-400' },
    Medium: { cls: 'text-amber-400 border-amber-500/40 bg-amber-500/10',  dot: 'bg-amber-400' },
    Low:    { cls: 'text-red-400   border-red-500/40   bg-red-500/10',    dot: 'bg-red-400'   },
  }[confidence] || { cls: 'text-[var(--text)] border-[var(--border)] bg-[var(--code-bg)]', dot: 'bg-[var(--text)]' }

  return (
    <div className="rounded-xl border border-[var(--accent)]/30 bg-[var(--bg)] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 px-4 py-3 bg-[var(--accent)]/8 border-b border-[var(--accent)]/20">
        <div className="flex items-center gap-2">
          <Ticket size={14} className="text-[var(--accent)] shrink-0" />
          <span className="text-sm font-bold text-[var(--text-h)]">Acca of the Day</span>
          <span className="text-[10px] text-[var(--text)] opacity-60">AI-selected accumulator</span>
        </div>
        <div className="flex items-center gap-2">
          {confidence && (
            <span className={`flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full border ${confCfg.cls}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${confCfg.dot}`} />
              {confidence}
            </span>
          )}
          {combined_odds && (
            <span className="text-sm font-bold text-[var(--accent)] tabular-nums">
              @{combined_odds}
            </span>
          )}
          {legs.length > 0 && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-semibold bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/25">
              <Zap size={10} />
              {tracked ? 'Auto-tracked · K50,000' : 'Auto-tracked · K50,000'}
            </span>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-3">
        {error && (
          <p className="text-xs text-red-400 opacity-80">Acca builder unavailable — {error}</p>
        )}

        {legs.length > 0 && (
          <div className="space-y-2">
            {legs.map((leg, i) => {
              const match = leg.home_team && leg.away_team
                ? `${leg.home_team} vs ${leg.away_team}`
                : leg.match_name || '—'
              return (
                <div key={i} className="flex items-start gap-3 rounded-lg border border-[var(--border)] bg-[var(--code-bg)] px-3 py-2.5">
                  {/* Leg number */}
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] text-[10px] font-bold flex items-center justify-center mt-0.5">
                    {i + 1}
                  </span>
                  <div className="flex-1 min-w-0 space-y-0.5">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-xs font-semibold text-[var(--text-h)] truncate">{match}</span>
                      {leg.market && (
                        <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-md bg-[var(--accent)]/15 text-[var(--accent)] font-semibold">
                          {leg.market}
                        </span>
                      )}
                    </div>
                    {leg.reason && (
                      <p className="text-[11px] text-[var(--text)] opacity-75 leading-snug">{leg.reason}</p>
                    )}
                  </div>
                  {leg.odd != null && (
                    <span className="shrink-0 text-xs font-bold text-[var(--text-h)] tabular-nums">{Number(leg.odd).toFixed(2)}</span>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {/* Combined odds footer */}
        {legs.length > 0 && combined_odds && (
          <div className="flex items-center justify-between pt-1 border-t border-[var(--border)] text-xs">
            <span className="text-[var(--text)] opacity-70">{legs.length} legs combined</span>
            <span className="font-bold text-[var(--accent)] tabular-nums">Combined odds: {combined_odds}</span>
          </div>
        )}

        {rationale && (
          <p className="text-[11px] text-[var(--text)] opacity-75 leading-relaxed border-t border-[var(--border)] pt-2">
            {rationale}
          </p>
        )}

        {!error && legs.length === 0 && (
          <p className="text-xs text-[var(--text)] opacity-60 text-center py-2">No accumulator generated for this date.</p>
        )}
      </div>
    </div>
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

  // In tab mode auto-run when the panel mounts or the date changes
  useEffect(() => {
    if (tabMode && (!data || lastDate !== date)) {
      runAnalysis()
    }
  }, [tabMode, date]) // eslint-disable-line

  async function runAnalysis() {
    if (loading) return
    if (data && lastDate === date) return

    setLoading(true)
    setError(null)
    try {
      const result = await fetchAdvisorInsights(date)
      setData(result)
      setLastDate(date)
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
          <div className="rounded-lg border border-[var(--border)] bg-[var(--code-bg)] px-4 py-3 text-sm text-[var(--text)] opacity-75">
            Advisor unavailable.
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

        {/* Acca of the Day — shown once analysis is loaded */}
        {!loading && data?.accumulator && (
          <AccaTicket acca={data.accumulator} />
        )}

        {/* Acca skeleton while loading */}
        {loading && (
          <div className="rounded-xl border border-[var(--accent)]/20 bg-[var(--bg)] overflow-hidden animate-pulse">
            <div className="flex items-center gap-2 px-4 py-3 bg-[var(--accent)]/8 border-b border-[var(--accent)]/20">
              <div className="w-3.5 h-3.5 rounded bg-[var(--border)]" />
              <div className="h-3 w-32 rounded bg-[var(--border)]" />
            </div>
            <div className="px-4 py-3 space-y-2">
              {[1,2,3].map(i => <div key={i} className="h-10 rounded-lg bg-[var(--border)]" />)}
            </div>
          </div>
        )}

        {(loading || data?.advisors?.length > 0) && (
          <div className="grid gap-4 sm:grid-cols-3">
            {loading
              ? ADVISORS_META.map(m => <AdvisorSkeleton key={m.id} {...m} />)
              : data.advisors.map(adv => <AdvisorCard key={adv.id} advisor={adv} onFilterPick={onFilterPick} />)
            }
          </div>
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
              onClick={runAnalysis}
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
            onClick={e => { e.stopPropagation(); runAnalysis() }}
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
