import { useState } from 'react'
import { X } from 'lucide-react'
import { trackPick } from '../../api/tracker'
import { fmtK } from '../../utils/format'

export default function TrackModal({ signal, bankroll, onClose, onTracked }) {
  const recPct = signal.dual_recommended_stake_pct ?? 0.01
  const recAmount = +((bankroll || 0) * recPct).toFixed(2)
  const bestOdd = signal.bayesian?.best_odd ?? null
  const bookmaker = signal.bayesian?.bookmaker ?? null
  const offeredOdds = Array.isArray(signal.bookmaker_odds) ? signal.bookmaker_odds : []
  const fallbackOption = bestOdd != null
    ? [{ bookmaker: bookmaker || 'Best available', odds: bestOdd }]
    : []

  const uniqueOptions = [...offeredOdds, ...fallbackOption].filter((option, index, all) => {
    const key = `${option.bookmaker}|${option.odds}`
    return all.findIndex(item => `${item.bookmaker}|${item.odds}` === key) === index
  })

  const defaultOption = uniqueOptions[0] ?? null

  const [stake, setStake] = useState(recAmount || 0)
  const [selectedOddsKey, setSelectedOddsKey] = useState(
    defaultOption ? `${defaultOption.bookmaker}|${defaultOption.odds}` : ''
  )
  const [manualOdds, setManualOdds] = useState('')
  const [manualBookmaker, setManualBookmaker] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const selectedOption = uniqueOptions.find(
    option => `${option.bookmaker}|${option.odds}` === selectedOddsKey
  ) ?? null

  const parsedStake = parseFloat(stake)
  const finalOdds = selectedOption ? Number(selectedOption.odds) : parseFloat(manualOdds)
  const finalBookmaker = selectedOption
    ? selectedOption.bookmaker
    : (manualBookmaker.trim() || 'Manual')
  const estimatedReturn = Number.isFinite(parsedStake) && finalOdds > 1
    ? +(parsedStake * finalOdds).toFixed(2)
    : null
  const estimatedProfit = Number.isFinite(parsedStake) && finalOdds > 1
    ? +(parsedStake * (finalOdds - 1)).toFixed(2)
    : null

  async function handleSubmit(e) {
    e.preventDefault()
    if (!Number.isFinite(finalOdds) || finalOdds <= 1) {
      setError('Please enter valid decimal odds greater than 1.0.')
      return
    }
    if (!Number.isFinite(parsedStake) || parsedStake <= 0) {
      setError('Please enter a valid stake amount.')
      return
    }

    setSaving(true)
    setError(null)
    try {
      await trackPick({
        fixture_id: signal.fixture_id,
        match_name: `${signal.home_team} vs ${signal.away_team}`,
        league: signal.league,
        event_date: signal.kickoff_at ? signal.kickoff_at.slice(0, 10) : undefined,
        market_type: signal.market,
        selection_name: signal.selection_name || signal.market,
        bookmaker: finalBookmaker,
        odds: finalOdds,
        stake: parsedStake,
        dual_confidence: signal.dual_confidence,
        recommended_stake_pct: signal.dual_recommended_stake_pct,
        signal_grade: signal.poisson?.grade ?? null,
        source_rule_key: signal.poisson?.rule_key ?? null,
        source_rule_label: signal.tracking_source_family ?? null,
        notes: notes.trim() || null,
      })
      onTracked?.()
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-[var(--bg)] rounded-2xl border border-[var(--border)] shadow-xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border)]">
          <div>
            <h3 className="font-semibold text-[var(--text-h)]">Bet Slip</h3>
            <p className="text-xs text-[var(--text)] opacity-70 mt-0.5">Lock the bookmaker price you actually want to track.</p>
          </div>
          <button onClick={onClose} className="text-[var(--text)] hover:text-[var(--text-h)]">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-5 py-4 space-y-4">
          <div className="space-y-1">
            <div className="text-sm font-medium text-[var(--text-h)]">
              {signal.home_team} vs {signal.away_team}
            </div>
            <div className="text-sm text-[var(--text)]">{signal.market}</div>
            <div className="text-xs text-[var(--text)] opacity-85">{signal.league}</div>
          </div>

          <div className="flex items-center justify-between text-xs text-[var(--text)]">
            <span>Recommended stake</span>
            <span className="font-semibold text-[var(--accent)]">{(recPct * 100).toFixed(1)}% = {fmtK(recAmount)}</span>
          </div>

          {uniqueOptions.length > 0 && (
            <label className="block">
              <span className="text-sm text-[var(--text)] mb-1 block">Bookmaker and price</span>
              <select
                value={selectedOddsKey}
                onChange={e => setSelectedOddsKey(e.target.value)}
                className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
              >
                {uniqueOptions.map(option => {
                  const value = `${option.bookmaker}|${option.odds}`
                  return (
                    <option key={value} value={value}>
                      {option.bookmaker} @ {Number(option.odds).toFixed(2)}
                    </option>
                  )
                })}
                <option value="">Manual entry</option>
              </select>
            </label>
          )}

          {!selectedOption && (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-sm text-[var(--text)] mb-1 block">Bookmaker</span>
                <input
                  type="text"
                  placeholder="e.g. Bet365"
                  value={manualBookmaker}
                  onChange={e => setManualBookmaker(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
                />
              </label>
              <label className="block">
                <span className="text-sm text-[var(--text)] mb-1 block">Odds</span>
                <input
                  type="number"
                  step="0.01"
                  min="1.01"
                  placeholder="e.g. 1.85"
                  value={manualOdds}
                  onChange={e => setManualOdds(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
                  required
                />
              </label>
            </div>
          )}

          <label className="block">
            <span className="text-sm text-[var(--text)] mb-1 block">Stake amount (K)</span>
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={stake}
              onChange={e => setStake(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
              required
            />
          </label>

          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-3 py-2">
              <div className="text-[var(--text)] opacity-70">Est. return</div>
              <div className="mt-1 font-semibold text-[var(--text-h)]">
                {estimatedReturn != null ? fmtK(estimatedReturn) : '-'}
              </div>
            </div>
            <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] px-3 py-2">
              <div className="text-[var(--text)] opacity-70">Est. profit</div>
              <div className="mt-1 font-semibold text-[var(--accent)]">
                {estimatedProfit != null ? fmtK(estimatedProfit) : '-'}
              </div>
            </div>
          </div>

          <label className="block">
            <span className="text-sm text-[var(--text)] mb-1 block">Notes</span>
            <textarea
              rows="2"
              value={notes}
              onChange={e => setNotes(e.target.value)}
              placeholder="Why you took this price, timing, or slip context"
              className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)] resize-none"
            />
          </label>

          {uniqueOptions.length === 0 && (
            <p className="text-xs text-[var(--text)] opacity-75">
              No bookmaker prices were attached to this signal, so this pick will be tracked from your manual entry.
            </p>
          )}

          {error && <p className="text-xs text-red-400">{error}</p>}

          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2 rounded-lg border border-[var(--border)] text-sm text-[var(--text)] hover:bg-[var(--code-bg)] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="flex-1 px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:opacity-90 disabled:opacity-60 transition-opacity"
            >
              {saving ? 'Saving...' : 'Save to Tracker'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
