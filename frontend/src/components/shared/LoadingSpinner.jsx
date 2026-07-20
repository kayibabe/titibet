export default function LoadingSpinner({ size = 'md', className = '' }) {
  const sz = { sm: 'w-4 h-4', md: 'w-6 h-6', lg: 'w-10 h-10' }[size]
  return (
    <div className={`animate-spin rounded-full border-2 border-[var(--border)] border-t-[var(--accent)] ${sz} ${className}`} />
  )
}
