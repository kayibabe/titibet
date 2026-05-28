/**
 * Shared formatting helpers used across the app.
 * Currency: Malawian Kwacha — symbol "K" prefix, e.g. K1,250.00
 * Time:     Always rendered in the user's local timezone via the browser's Intl APIs.
 */

/**
 * Ensure an ISO datetime string from the backend is treated as UTC.
 * The backend stores naive datetimes without a timezone suffix; appending 'Z'
 * forces JS to parse them as UTC so toLocale* methods return correct local time.
 */
export function toUtcDate(isoStr) {
  if (!isoStr) return null
  const utc = isoStr.endsWith('Z') || isoStr.includes('+') ? isoStr : isoStr + 'Z'
  const d = new Date(utc)
  return isNaN(d) ? null : d
}

/** Format a UTC ISO string as a short local date, e.g. "11 May 2026" */
export function fmtDate(isoStr) {
  const d = toUtcDate(isoStr)
  if (!d) return '—'
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** Format an amount as Kwacha, e.g. K1,250.00 */
export function fmtK(amount, decimals = 2) {
  if (amount == null || isNaN(amount)) return '—'
  const abs = Math.abs(amount)
  const formatted = abs.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
  const sign = amount < 0 ? '-' : ''
  return `${sign}K${formatted}`
}

/** Format a signed P&L with +/- prefix, e.g. +K250.00 or -K80.00 */
export function fmtPL(amount, decimals = 2) {
  if (amount == null || isNaN(amount)) return '—'
  const abs = Math.abs(amount)
  const formatted = abs.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
  const sign = amount >= 0 ? '+' : '-'
  return `${sign}K${formatted}`
}

/**
 * Compact P&L for tight spaces (KPI cards, stat bars).
 * Abbreviates large amounts so the string stays ≤ 8 chars:
 *   +K45,849.10  →  +K45.8k
 *   -K1,250.00   →  -K1.25k
 *   +K320.50     →  +K320.50  (unchanged when small)
 */
export function fmtPLCompact(amount) {
  if (amount == null || isNaN(amount)) return '—'
  const abs = Math.abs(amount)
  const sign = amount >= 0 ? '+' : '-'
  if (abs >= 1_000_000) return `${sign}K${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000)    return `${sign}K${(abs / 1_000).toFixed(1)}k`
  if (abs >= 1_000)     return `${sign}K${(abs / 1_000).toFixed(2)}k`
  return `${sign}K${abs.toFixed(2)}`
}

/**
 * Compact Kwacha amount for tight spaces (no sign prefix).
 *   67200  →  K67.2k
 *   1250   →  K1.25k
 *   320    →  K320.00
 */
export function fmtKCompact(amount) {
  if (amount == null || isNaN(amount)) return '—'
  const abs = Math.abs(amount)
  const sign = amount < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}K${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000)    return `${sign}K${(abs / 1_000).toFixed(1)}k`
  if (abs >= 1_000)     return `${sign}K${(abs / 1_000).toFixed(2)}k`
  return `${sign}K${abs.toFixed(2)}`
}

/**
 * Format a UTC ISO datetime string as the user's local time, 12-hour clock.
 * Appends Z if the string has no timezone marker so JS parses it as UTC.
 * Returns e.g. "2:30 PM" for today, "Sun 2:30 PM" for another day this week,
 * "11 May 2:30 PM" for further out.
 */
export function fmtKickoff(isoString) {
  if (!isoString) return null
  // Ensure UTC interpretation — backend stores naive datetimes without Z
  const utc = isoString.endsWith('Z') || isoString.includes('+') ? isoString : isoString + 'Z'
  const date = new Date(utc)
  if (isNaN(date)) return null

  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const tomorrowStart = new Date(todayStart.getTime() + 86400000)
  const weekOut = new Date(todayStart.getTime() + 7 * 86400000)

  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true })

  if (date >= todayStart && date < tomorrowStart) {
    return time                                                        // "2:30 PM"
  }
  if (date >= tomorrowStart && date < weekOut) {
    const day = date.toLocaleDateString([], { weekday: 'short' })
    return `${day} ${time}`                                            // "Sun 2:30 PM"
  }
  const dayMonth = date.toLocaleDateString([], { day: 'numeric', month: 'short' })
  return `${dayMonth} ${time}`                                         // "18 May 2:30 PM"
}

/**
 * Returns milliseconds until the given ISO datetime (stored as UTC).
 * Negative if already past.
 */
export function msUntil(isoString) {
  if (!isoString) return null
  const utc = isoString.endsWith('Z') || isoString.includes('+') ? isoString : isoString + 'Z'
  return new Date(utc) - Date.now()
}
