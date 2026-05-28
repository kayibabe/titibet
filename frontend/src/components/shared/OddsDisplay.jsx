export default function OddsDisplay({ odds, bookmaker, className = '' }) {
  if (!odds) return <span className="text-[var(--text)] opacity-65">—</span>
  return (
    <span className={`font-mono font-semibold text-[var(--text-h)] ${className}`}>
      {Number(odds).toFixed(2)}
      {bookmaker && (
        <span className="ml-1 text-xs font-normal text-[var(--text)] opacity-85">@{bookmaker}</span>
      )}
    </span>
  )
}
