import { useState } from 'react'
import { Play } from 'lucide-react'

const MARKETS = [
  { value: '',                  label: 'All Markets' },
  { value: 'Over 0.5',          label: 'Over 0.5' },
  { value: 'Over 1.5',          label: 'Over 1.5' },
  { value: 'Over 2.5',          label: 'Over 2.5' },
  { value: 'Over 3.5',          label: 'Over 3.5' },
  { value: 'Under 1.5',         label: 'Under 1.5' },
  { value: 'Under 2.5',         label: 'Under 2.5' },
  { value: 'Under 3.5',         label: 'Under 3.5' },
  { value: 'BTTS Yes',          label: 'BTTS Yes' },
  { value: 'BTTS No',           label: 'BTTS No' },
  { value: '1X (Home or Draw)', label: '1X (Home or Draw)' },
  { value: 'X2 (Draw or Away)', label: 'X2 (Draw or Away)' },
  { value: '12 (Home or Away)', label: '12 (Home or Away)' },
  { value: 'Home Over 0.5',     label: 'Home Over 0.5' },
  { value: 'Home Under 0.5',    label: 'Home Under 0.5' },
  { value: 'Home Over 1.5',     label: 'Home Over 1.5' },
  { value: 'Home Under 1.5',    label: 'Home Under 1.5' },
  { value: 'Away Over 0.5',     label: 'Away Over 0.5' },
  { value: 'Away Under 0.5',    label: 'Away Under 0.5' },
  { value: 'Away Over 1.5',     label: 'Away Over 1.5' },
  { value: 'Away Under 1.5',    label: 'Away Under 1.5' },
  { value: 'Home Win to Nil',   label: 'Home Win to Nil' },
  { value: 'Away Win to Nil',   label: 'Away Win to Nil' },
  { value: 'Exactly 1 Goal',    label: 'Exactly 1 Goal' },
  { value: 'Exactly 2 Goals',   label: 'Exactly 2 Goals' },
  { value: 'Exactly 3 Goals',   label: 'Exactly 3 Goals' },
]

const ENGINES = [
  { value: 'dual',     label: 'Dual (Both)' },
  { value: 'bayesian', label: 'Bayesian Only' },
  { value: 'poisson',  label: 'Poisson Only' },
]

const DATE_PRESETS = [
  { label: '3m', months: 3 },
  { label: '6m', months: 6 },
  { label: '1y', months: 12 },
  { label: '2y', months: 24 },
]

function monthsAgo(n) {
  const d = new Date()
  d.setMonth(d.getMonth() - n)
  return d.toISOString().slice(0, 10)
}

const inputCls = 'px-2.5 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]'

function Field({ label, children, className = '' }) {
  return (
    <label className={`flex flex-col gap-1 ${className}`}>
      <span className="text-[11px] font-semibold text-[var(--text)] opacity-65 uppercase tracking-wide">{label}</span>
      {children}
    </label>
  )
}

export default function BacktestControls({ onRun, loading }) {
  const today = new Date().toISOString().slice(0, 10)

  const [form, setForm] = useState({
    market:    '',
    league_id: '',
    min_edge:  5,
    date_from: monthsAgo(12),
    date_to:   today,
    engine:    'dual',
    confidence: new Set(['High', 'Medium']),
  })
  const [activePreset, setActivePreset] = useState('1y')

  function set(key, val) { setForm(prev => ({ ...prev, [key]: val })) }

  function toggleConfidence(tier) {
    setForm(prev => {
      const next = new Set(prev.confidence)
      if (next.has(tier)) {
        if (next.size > 1) next.delete(tier)
      } else {
        next.add(tier)
      }
      return { ...prev, confidence: next }
    })
  }

  function applyPreset(p) {
    setActivePreset(p.label)
    set('date_from', monthsAgo(p.months))
    set('date_to', today)
  }

  function handleSubmit(e) {
    e.preventDefault()
    onRun({
      market:            form.market || null,
      league_id:         form.league_id || null,
      min_edge:          parseFloat(form.min_edge) / 100,
      date_from:         form.date_from,
      date_to:           form.date_to,
      engine:            form.engine,
      confidence_filter: [...form.confidence].join(','),
      _labels: {
        market:     form.market || 'All Markets',
        engine:     ENGINES.find(e => e.value === form.engine)?.label ?? form.engine,
        confidence: [...form.confidence].join(' + '),
        min_edge:   `${form.min_edge}% min edge`,
      },
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* Row 1: market, engine, min edge, league */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Field label="Market">
          <select value={form.market} onChange={e => set('market', e.target.value)} className={inputCls}>
            {MARKETS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </Field>
        <Field label="Engine">
          <select value={form.engine} onChange={e => set('engine', e.target.value)} className={inputCls}>
            {ENGINES.map(e => <option key={e.value} value={e.value}>{e.label}</option>)}
          </select>
        </Field>
        <Field label="Min edge (%)">
          <input type="number" min="1" max="20" step="0.5" value={form.min_edge}
            onChange={e => set('min_edge', e.target.value)} className={inputCls} />
        </Field>
        <Field label="League ID (optional)">
          <input type="text" placeholder="e.g. 39 for EPL" value={form.league_id}
            onChange={e => set('league_id', e.target.value)} className={inputCls} />
        </Field>
      </div>

      {/* Row 2: date range + presets */}
      <div className="flex flex-wrap items-end gap-3">
        <Field label="From">
          <input type="date" value={form.date_from}
            onChange={e => { set('date_from', e.target.value); setActivePreset('') }} className={inputCls} />
        </Field>
        <Field label="To">
          <input type="date" value={form.date_to}
            onChange={e => { set('date_to', e.target.value); setActivePreset('') }} className={inputCls} />
        </Field>
        <div className="flex items-end gap-1 pb-0.5">
          {DATE_PRESETS.map(p => (
            <button
              key={p.label}
              type="button"
              onClick={() => applyPreset(p)}
              className={'px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors ' + (
                activePreset === p.label
                  ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--accent-bg,rgba(99,102,241,0.1))]'
                  : 'border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Row 3: confidence filter + run button */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-[var(--border)]">
        <div className="flex items-center gap-3">
          <span className="text-[11px] font-semibold text-[var(--text)] opacity-65 uppercase tracking-wide">
            Confidence
          </span>
          <div className="flex gap-1.5">
            {[
              { tier: 'High',   ring: 'border-green-500/50  bg-green-500/10  text-green-400' },
              { tier: 'Medium', ring: 'border-yellow-500/50 bg-yellow-500/10 text-yellow-400' },
              { tier: 'Low',    ring: 'border-orange-500/50 bg-orange-500/10 text-orange-400' },
            ].map(({ tier, ring }) => {
              const active = form.confidence.has(tier)
              return (
                <button
                  key={tier}
                  type="button"
                  onClick={() => toggleConfidence(tier)}
                  className={'px-2.5 py-1 rounded-full text-xs font-semibold border transition-all ' + (
                    active
                      ? ring
                      : 'border-[var(--border)] text-[var(--text)] opacity-45 hover:opacity-70'
                  )}
                >
                  {tier}
                </button>
              )
            })}
          </div>
          <span className="text-[10px] text-[var(--text)] opacity-65">
            (at least one required)
          </span>
        </div>

        <button
          type="submit"
          disabled={loading}
          className="flex items-center gap-2 px-5 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          <Play size={14} />
          {loading ? 'Running…' : 'Run Backtest'}
        </button>
      </div>
    </form>
  )
}
