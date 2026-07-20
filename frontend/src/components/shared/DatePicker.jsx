export default function DatePicker({ value, onChange, label, className = '' }) {
  return (
    <label className={`flex flex-col gap-1 text-sm text-[var(--text)] ${className}`}>
      {label && <span className="font-medium">{label}</span>}
      <input
        type="date"
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
      />
    </label>
  )
}
