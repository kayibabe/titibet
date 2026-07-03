import { useState } from 'react'
import { Save, Monitor, Sun, Moon } from 'lucide-react'

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


export default function SettingsPage({ settings, onUpdate }) {
  const [saved, setSaved] = useState(false)

  function handleSave(e) {
    e.preventDefault()
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="space-y-6 max-w-xl">
      <form onSubmit={handleSave} className="space-y-6">

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
