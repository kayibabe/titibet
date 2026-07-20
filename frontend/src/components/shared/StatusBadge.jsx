const STYLES = {
  Won: 'bg-green-500/15 text-green-400 border-green-500/30',
  won: 'bg-green-500/15 text-green-400 border-green-500/30',
  Lost: 'bg-red-500/15 text-red-400 border-red-500/30',
  lost: 'bg-red-500/15 text-red-400 border-red-500/30',
  Void: 'bg-gray-500/15 text-gray-400 border-gray-500/30',
  void: 'bg-gray-500/15 text-gray-400 border-gray-500/30',
  Pending: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
  pending: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
  High: 'bg-[var(--accent-bg)] text-[var(--accent)] border-[var(--accent-border)]',
  Medium: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  Low: 'bg-gray-500/15 text-gray-400 border-gray-500/30',
  None: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
  Both: 'bg-green-500/15 text-green-400 border-green-500/30',
  'Bayesian Only': 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  'Poisson Only': 'bg-purple-500/15 text-purple-400 border-purple-500/30',
  Contradiction: 'bg-red-500/15 text-red-400 border-red-500/30',
}

export default function StatusBadge({ status, className = '' }) {
  const style = STYLES[status] || 'bg-gray-500/15 text-gray-400 border-gray-500/30'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border ${style} ${className}`}>
      {status}
    </span>
  )
}
