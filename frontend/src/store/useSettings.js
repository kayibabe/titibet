import { useState, useEffect } from 'react'

const DEFAULTS = {
  bankroll: 50000,
  unitPct: 1,
  kellyFraction: 0.25,
  theme: 'system',
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
