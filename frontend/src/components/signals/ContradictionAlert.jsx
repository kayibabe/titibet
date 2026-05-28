import { AlertTriangle } from 'lucide-react'

export default function ContradictionAlert({ mixedSignals = [] }) {
  return (
    <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-sm">
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-red-400" />
      <div>
        <p className="text-sm font-semibold text-red-400">⚡ Model contradiction detected</p>
        <p className="text-xs text-slate-400 mt-0.5">The Bayesian and Poisson models disagree on this outcome. Review carefully — lower your stake or skip.</p>
        {mixedSignals?.length > 0 && (
          <p className="text-xs text-red-400/80 mt-1">{mixedSignals.join(' · ')}</p>
        )}
      </div>
    </div>
  )
}
