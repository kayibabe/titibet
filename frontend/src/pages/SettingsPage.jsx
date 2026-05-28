import { useState } from 'react'
import { Save, Monitor, Sun, Moon, Shield, Target, Zap, Globe } from 'lucide-react'

const RISK_PRESETS = [
  {
    id: 'conservative',
    label: 'Conservative',
    Icon: Shield,
    description: 'High confidence, dual agreement only — fewer picks, higher precision',
    values: { defaultConfidence: 'High', defaultAgreement: 'Both', minQuality: 0.30, hideContradictions: true },
  },
  {
    id: 'balanced',
    label: 'Balanced',
    Icon: Target,
    description: 'High + Medium confidence, any agreement — good volume/quality mix',
    values: { defaultConfidence: 'High,Medium', defaultAgreement: '', minQuality: 0.20, hideContradictions: true },
  },
  {
    id: 'aggressive',
    label: 'Aggressive',
    Icon: Zap,
    description: 'All confidence tiers, any agreement — wider net, more bets',
    values: { defaultConfidence: '', defaultAgreement: '', minQuality: 0.0, hideContradictions: false },
  },
]

const CONFIDENCE_TIERS = [
  { value: 'High',   cls: 'border-green-500/50 bg-green-500/10 text-green-400' },
  { value: 'Medium', cls: 'border-yellow-500/50 bg-yellow-500/10 text-yellow-400' },
  { value: 'Low',    cls: 'border-orange-500/50 bg-orange-500/10 text-orange-400' },
]

const AGREEMENT_OPTIONS = [
  { value: '',              label: 'All Agreement Types' },
  { value: 'Both',          label: 'Both Engines (highest precision)' },
  { value: 'Bayesian Only', label: 'Bayesian Only' },
  { value: 'Poisson Only',  label: 'Poisson Only' },
]

const THEME_OPTIONS = [
  { value: 'system', label: 'System', Icon: Monitor },
  { value: 'light',  label: 'Light',  Icon: Sun },
  { value: 'dark',   label: 'Dark',   Icon: Moon },
]

function Section({ title, hint, children }) {
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5 space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-[var(--text-h)]">{title}</h2>
        {hint && <p className="text-xs text-[var(--text)] opacity-55 mt-0.5">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

function Field({ label, hint, children }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium text-[var(--text-h)]">{label}</label>
      {hint && <p className="text-xs text-[var(--text)] opacity-55">{hint}</p>}
      {children}
    </div>
  )
}

function RangeField({ label, hint, min, max, step, value, onChange, display }) {
  return (
    <Field label={`${label}: ${display}`} hint={hint}>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full accent-[var(--accent)]"
      />
      <div className="flex justify-between text-xs text-[var(--text)] opacity-70 mt-0.5">
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </Field>
  )
}

function Toggle({ value, onChange, label, hint }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-sm font-medium text-[var(--text-h)]">{label}</p>
        {hint && <p className="text-xs text-[var(--text)] opacity-55 mt-0.5">{hint}</p>}
      </div>
      <button
        type="button"
        onClick={() => onChange(!value)}
        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none ${
          value ? 'bg-[var(--accent)]' : 'bg-[var(--border)]'
        }`}
      >
        <span
          className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
            value ? 'translate-x-4' : 'translate-x-0'
          }`}
        />
      </button>
    </div>
  )
}

function activePresetId(settings) {
  return RISK_PRESETS.find(p =>
    p.values.defaultConfidence === settings.defaultConfidence &&
    p.values.defaultAgreement  === settings.defaultAgreement  &&
    p.values.minQuality        === settings.minQuality        &&
    p.values.hideContradictions === settings.hideContradictions
  )?.id ?? null
}

export default function SettingsPage({ settings, onUpdate }) {
  const [saved, setSaved] = useState(false)

  function handleSave(e) {
    e.preventDefault()
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  function applyPreset(preset) {
    Object.entries(preset.values).forEach(([k, v]) => onUpdate(k, v))
  }

  const confidenceSet = new Set(
    (settings.defaultConfidence || '').split(',').filter(Boolean)
  )

  function toggleConfidence(tier) {
    const next = new Set(confidenceSet)
    if (next.has(tier)) next.delete(tier)
    else next.add(tier)
    onUpdate('defaultConfidence', [...next].join(','))
  }

  const preset = activePresetId(settings)

  return (
    <div className="space-y-6 max-w-xl">
      <form onSubmit={handleSave} className="space-y-6">

        {/* ── Risk Profile Presets ─────────────────────────────── */}
        <Section
          title="Risk Profile"
          hint="Quickly configure your signal filter preferences — or fine-tune them individually below"
        >
          <div className="grid grid-cols-3 gap-3">
            {RISK_PRESETS.map(p => {
              const active = preset === p.id
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => applyPreset(p)}
                  className={`flex flex-col items-start gap-1.5 p-3 rounded-lg border text-left transition-colors ${
                    active
                      ? 'border-[var(--accent)] bg-[var(--accent-bg,rgba(99,102,241,0.08))]'
                      : 'border-[var(--border)] hover:border-[var(--accent)]/40 hover:bg-[var(--code-bg)]'
                  }`}
                >
                  <p.Icon
                    size={14}
                    className={active ? 'text-[var(--accent)]' : 'text-[var(--text)] opacity-70'}
                  />
                  <span className={`text-xs font-semibold ${active ? 'text-[var(--accent)]' : 'text-[var(--text-h)]'}`}>
                    {p.label}
                  </span>
                  <span className="text-[10px] text-[var(--text)] opacity-55 leading-tight">
                    {p.description}
                  </span>
                </button>
              )
            })}
          </div>
        </Section>

        {/* ── Signal Filters ───────────────────────────────────── */}
        <Section
          title="Signal Filters"
          hint="Applied as defaults on the Signals page — you can still adjust them there per session"
        >
          <Field
            label="Default Confidence"
            hint="Show only signals at or above the selected confidence tier(s)"
          >
            <div className="flex flex-wrap gap-1.5 mt-1">
              <button
                type="button"
                onClick={() => onUpdate('defaultConfidence', '')}
                className={`px-2.5 py-1 rounded-full text-xs font-semibold border transition-all ${
                  confidenceSet.size === 0
                    ? 'border-[var(--accent)] bg-[var(--accent-bg,rgba(99,102,241,0.08))] text-[var(--accent)]'
                    : 'border-[var(--border)] text-[var(--text)] opacity-70 hover:opacity-80'
                }`}
              >
                All
              </button>
              {CONFIDENCE_TIERS.map(({ value, cls }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => toggleConfidence(value)}
                  className={`px-2.5 py-1 rounded-full text-xs font-semibold border transition-all ${
                    confidenceSet.has(value)
                      ? cls
                      : 'border-[var(--border)] text-[var(--text)] opacity-45 hover:opacity-75'
                  }`}
                >
                  {value}
                </button>
              ))}
            </div>
          </Field>

          <Field
            label="Default Agreement"
            hint="Both Engines (dual agreement) signals have the highest hit rate"
          >
            <select
              value={settings.defaultAgreement}
              onChange={e => onUpdate('defaultAgreement', e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]"
            >
              {AGREEMENT_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </Field>

          <RangeField
            label="Min quality score"
            hint="Fused signal quality (edge × probability × tier factors). ≥0.30 indicates both-engine agreement with strong conviction — the sweet spot for precision."
            min={0}
            max={0.8}
            step={0.05}
            value={settings.minQuality}
            onChange={v => onUpdate('minQuality', v)}
            display={settings.minQuality === 0 ? 'Off' : settings.minQuality.toFixed(2)}
          />

          <Toggle
            value={settings.hideContradictions}
            onChange={v => onUpdate('hideContradictions', v)}
            label="Hide contradictions"
            hint="Remove signals where the two engines disagree on direction — these rank lowest by the system"
          />
        </Section>

        {/* ── Appearance ──────────────────────────────────────── */}
        <Section title="Appearance">
          <Field label="Theme" hint="Override the system colour scheme">
            <div className="flex gap-2 mt-1">
              {THEME_OPTIONS.map(({ value, label, Icon }) => {
                const active = settings.theme === value
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => onUpdate('theme', value)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${
                      active
                        ? 'border-[var(--accent)] bg-[var(--accent-bg,rgba(99,102,241,0.08))] text-[var(--accent)]'
                        : 'border-[var(--border)] text-[var(--text)] hover:text-[var(--text-h)] hover:bg-[var(--code-bg)]'
                    }`}
                  >
                    <Icon size={14} />
                    {label}
                  </button>
                )
              })}
            </div>
          </Field>
        </Section>

        {/* ── Bookmaker Odds Adjustment ───────────────────────── */}
        <Section
          title="Bookmaker Odds Adjustment"
          hint="The system now displays William Hill odds (~10% margin) as the closest available proxy. Betpawa, 888bets, Premier Bet MW and Moors Bet are not in the data source. Apply a further discount to approximate what those books actually offer vs William Hill."
        >
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {[
              { label: 'No discount',      pct: 0,  desc: 'Show William Hill odds as-is' },
              { label: 'Betpawa / 888bets',pct: 10, desc: 'African books ~10% more margin' },
              { label: 'Premier Bet MW',   pct: 15, desc: 'Higher margin regional book' },
              { label: 'Custom',           pct: null },
            ].map(opt => {
              const isCustom = opt.pct === null
              const active = isCustom
                ? ![0, 10, 20].includes(settings.oddsAdjustmentPct)
                : settings.oddsAdjustmentPct === opt.pct
              return (
                <button
                  key={opt.label}
                  type="button"
                  onClick={() => !isCustom && onUpdate('oddsAdjustmentPct', opt.pct)}
                  className={`flex flex-col items-start gap-0.5 p-3 rounded-lg border text-left transition-colors ${
                    active
                      ? 'border-[var(--accent)] bg-[var(--accent-bg,rgba(99,102,241,0.08))]'
                      : 'border-[var(--border)] hover:border-[var(--accent)]/40 hover:bg-[var(--code-bg)]'
                  } ${isCustom ? 'cursor-default' : ''}`}
                >
                  <Globe size={12} className={active ? 'text-[var(--accent)]' : 'text-[var(--text)] opacity-65'} />
                  <span className={`text-xs font-semibold mt-0.5 ${active ? 'text-[var(--accent)]' : 'text-[var(--text-h)]'}`}>{opt.label}</span>
                  {opt.desc && <span className="text-[10px] text-[var(--text)] opacity-70">{opt.desc}</span>}
                  {isCustom && active && (
                    <span className="text-[10px] text-[var(--accent)] font-semibold">−{settings.oddsAdjustmentPct}%</span>
                  )}
                </button>
              )
            })}
          </div>
          <RangeField
            label="Discount"
            hint="Odds shown and EV calculations will use: adjusted_odd = raw_odd × (1 − discount%)"
            min={0}
            max={35}
            step={1}
            value={settings.oddsAdjustmentPct}
            onChange={v => onUpdate('oddsAdjustmentPct', v)}
            display={settings.oddsAdjustmentPct === 0 ? 'None (Pinnacle)' : `−${settings.oddsAdjustmentPct}%`}
          />
        </Section>

        {/* ── Bankroll & Staking ──────────────────────────────── */}
        <Section
          title="Bankroll & Staking"
          hint="Controls stake display in the tracker — adjust to match your actual bankroll"
        >
          <Field label="Bankroll (K)" hint="Your current bankroll in Malawian Kwacha">
            <input
              type="number"
              min="1"
              step="1"
              value={settings.bankroll}
              onChange={e => onUpdate('bankroll', parseFloat(e.target.value))}
              className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
            />
          </Field>
          <RangeField
            label="Unit size"
            hint="% of bankroll per unit — used for unit-based stake display in the tracker"
            min={0.5}
            max={5}
            step={0.5}
            value={settings.unitPct}
            onChange={v => onUpdate('unitPct', v)}
            display={`${settings.unitPct}%`}
          />
          <RangeField
            label="Kelly fraction"
            hint="Half Kelly (0.50) roughly halves drawdown variance vs full Kelly. The engine's internal default is Quarter Kelly (0.25)."
            min={0.1}
            max={1.0}
            step={0.05}
            value={settings.kellyFraction}
            onChange={v => onUpdate('kellyFraction', v)}
            display={
              settings.kellyFraction === 1.0  ? 'Full Kelly' :
              settings.kellyFraction === 0.5  ? 'Half Kelly (0.50)' :
              settings.kellyFraction === 0.25 ? 'Quarter Kelly (0.25)' :
              settings.kellyFraction.toFixed(2)
            }
          />
        </Section>

        <button
          type="submit"
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 transition-opacity"
        >
          <Save size={14} />
          {saved ? 'Saved!' : 'Save Settings'}
        </button>
      </form>
    </div>
  )
}
