import { useState, useEffect } from 'react'

const DEFAULTS = {
  bankroll: 1000,
  unitPct: 1,
  kellyFraction: 0.25,
  // Signal filters — wired to the /api/signals query params
  defaultConfidence: '',      // '' = All, or comma-sep e.g. 'High,Medium'
  defaultAgreement: '',       // '' = All, or 'Both', 'Bayesian Only', 'Poisson Only'
  minQuality: 0.0,            // min dual_quality_score (0 = off, 0.40+ = Both engines)
  hideContradictions: true,   // client-side filter
  theme: 'system',
  oddsAdjustmentPct: 0,       // % to discount Pinnacle odds for local bookmaker parity (0 = none, 20 = African regional)
}

function applyTheme(theme) {
  const root = document.documentElement
  if (theme === 'dark') {
    root.setAttribute('data-theme', 'dark')
  } else if (theme === 'light') {
    root.setAttribute('data-theme', 'light')
  } else {
    root.removeAttribute('data-theme')
  }
}

export function useSettings() {
  const [settings, setSettings] = useState(() => {
    try {
      const saved = localStorage.getItem('titibet_settings')
      const merged = saved ? { ...DEFAULTS, ...JSON.parse(saved) } : DEFAULTS
      applyTheme(merged.theme)
      return merged
    } catch {
      return DEFAULTS
    }
  })

  useEffect(() => {
    localStorage.setItem('titibet_settings', JSON.stringify(settings))
    applyTheme(settings.theme)
  }, [settings])

  function update(key, value) {
    setSettings(prev => ({ ...prev, [key]: value }))
  }

  return { settings, update }
}
