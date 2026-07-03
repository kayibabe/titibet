import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { ConfidenceBadge, AgreementBadge } from './SignalBadge'
import OddsDisplay from '../shared/OddsDisplay'

// Bookmaker short-names for display
const BOOKIE_LABELS = {
  'Bet365': 'Bet365',
  'bet365': 'Bet365',
  '1xBet': '1xBet',
  '1xbet': '1xBet',
  'Betway': 'Betway',
  'William Hill': 'William Hill',
  'Pinnacle': 'Pinnacle',
  'Unibet': 'Unibet',
  'Bwin': 'Bwin',
}

function label(bookie) {
  return BOOKIE_LABELS[bookie] || bookie
}

function OddsChip({ bookmaker, odds, isBest }) {
  return (
    <div className={`flex flex-col items-center px-2.5 py-1.5 rounded-lg border text-xs transition-colors ${
      isBest
        ? 'border-[var(--accent-border)] bg-[var(--accent-bg)] text-[var(--accent)]'
        : 'border-[var(--border)] bg-[var(--code-bg)] text-[var(--text)]'
    }`}>
      <span className="font-mono font-semibold text-sm">{odds?.toFixed(2)}</span>
      <span className="opacity-85 mt-0.5 whitespace-nowrap">{label(bookmaker)}</span>
    </div>
  )
}

export default function MarketRow({ signal, onTrack }) {
  const [showOdds, setShowOdds] = useState(false)

  const fairOdds = signal.bayesian?.prob ? (1 / signal.bayesian.prob).toFixed(2) : null
  const overroundPct = signal.bayesian?.overround
    ? ((signal.bayesian.overround - 1) * 100).toFixed(1)
    : null

  const qualityPct = signal.dual_quality_score != null
    ? `${(signal.dual_quality_score * 100).toFixed(0)}%`
    : '—'
  const stakePct = signal.dual_recommended_stake_pct != null
    ? `${(signal.dual_recommended_stake_pct * 100).toFixed(1)}%`
    : '—'

  const hasBookmakerOdds = signal.bookmaker_odds?.length > 0
  // Deduplicate by bookmaker name keeping highest odds
  const dedupedOdds = signal.bookmaker_odds
    ? Object.values(
        signal.bookmaker_odds.reduce((acc, bo) => {
          const key = bo.bookmaker
          if (!acc[key] || bo.odds > acc[key].odds) acc[key] = bo
          return acc
        }, {})
      ).sort((a, b) => b.odds - a.odds)
    : []

  return (
    <>
      <tr className="border-t border-[var(--border)] hover:bg-[var(--code-bg)] transition-colors">
        <td className="px-3 py-2 text-sm text-[var(--text-h)] font-medium">{signal.market}</td>
        <td className="px-3 py-2"><ConfidenceBadge confidence={signal.dual_confidence} /></td>
        <td className="px-3 py-2"><AgreementBadge agreement={signal.dual_agreement} /></td>

        {/* Fair → Offered odds */}
        <td className="px-3 py-2">
          <div className="flex items-center gap-1.5 flex-wrap">
            {fairOdds && (
              <span className="text-xs text-[var(--text)]">
                <span className="font-mono font-semibold text-[var(--text-h)]">{fairOdds}</span>
                <span className="opacity-75 mx-1">→</span>
              </span>
            )}
            <OddsDisplay odds={signal.bayesian?.best_odd} bookmaker={signal.bayesian?.bookmaker} />
            {hasBookmakerOdds && (
              <button
                onClick={() => setShowOdds(v => !v)}
                className="text-[var(--text)] opacity-65 hover:opacity-80 transition-opacity"
                title="Show all bookmaker odds"
              >
                {showOdds ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </button>
            )}
          </div>
          {overroundPct && (
            <div className={`text-[10px] mt-0.5 ${
              parseFloat(overroundPct) < 5 ? 'text-green-400' : 'text-[var(--text)] opacity-75'
            }`}>
              {overroundPct}% margin
            </div>
          )}
        </td>

        <td className="px-3 py-2 text-xs font-mono text-[var(--text)]">{qualityPct}</td>
        <td className="px-3 py-2 text-xs font-mono text-[var(--accent)]">{stakePct}</td>
        <td className="px-3 py-2">
          {onTrack && signal.dual_confidence !== 'None' && (
            <button
              onClick={() => onTrack(signal)}
              className="text-xs px-2 py-1 rounded border border-[var(--accent-border)] text-[var(--accent)] hover:bg-[var(--accent-bg)] transition-colors"
            >
              Track
            </button>
          )}
        </td>
      </tr>

      {/* Bookmaker odds comparison panel */}
      {showOdds && hasBookmakerOdds && (
        <tr className="border-t border-[var(--border)] bg-[var(--code-bg)]">
          <td colSpan={8} className="px-3 py-3">
            <div className="flex items-start gap-1.5 flex-wrap">
              <span className="text-xs text-[var(--text)] opacity-75 mr-1 self-center">Live odds:</span>
              {dedupedOdds.map((bo, i) => (
                <OddsChip
                  key={`${bo.bookmaker}-${i}`}
                  bookmaker={bo.bookmaker}
                  odds={bo.odds}
                  isBest={i === 0}
                />
              ))}
            </div>
            {signal.selection_name && (
              <p className="text-xs text-[var(--text)] opacity-65 mt-1.5">
                Selection: <span className="italic">{signal.selection_name}</span>
              </p>
            )}
          </td>
        </tr>
      )}
    </>
  )
}
