const CONF_STYLES = {
  High: 'bg-[var(--accent-bg)] text-[var(--accent)] border-[var(--accent-border)] font-semibold',
  Medium: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  Low: 'bg-gray-500/15 text-gray-400 border-gray-500/30',
  None: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
}

const AGREE_STYLES = {
  Both: 'bg-green-500/15 text-green-400 border-green-500/30',
  'Bayesian Only': 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  'Poisson Only': 'bg-purple-500/15 text-purple-400 border-purple-500/30',
  Contradiction: 'bg-red-500/15 text-red-400 border-red-500/30',
  None: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
}

export function ConfidenceBadge({ confidence }) {
  const style = CONF_STYLES[confidence] || CONF_STYLES.None
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs border ${style}`}>
      {confidence}
    </span>
  )
}

const AGREE_LABELS = {
  Both:            'Dual Confirmed',
  'Bayesian Only': 'Market Analysis',
  'Poisson Only':  'Stats Model',
  Contradiction:   'Mixed Signals',
}

export function AgreementBadge({ agreement, raw = false }) {
  const style = AGREE_STYLES[agreement] || AGREE_STYLES.None
  const label = raw ? agreement : (AGREE_LABELS[agreement] ?? agreement)
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs border ${style}`}>
      {label}
    </span>
  )
}
