import { useState, useCallback } from 'react'
import { Send, RefreshCw, CheckCircle, XCircle, ChevronDown, ChevronRight, BarChart2 } from 'lucide-react'
import { fetchTelegramPreview, pushTelegramSignals, testTelegramSetup, pushTelegramResults } from '../../api/admin'

const PROFILE_COLOR = {
  conservative: 'text-blue-400',
  balanced:     'text-amber-400',
  aggressive:   'text-red-400',
}

const CONF_BADGE = {
  High:   'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  Medium: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  Low:    'bg-slate-500/15 text-slate-400 border-slate-500/30',
}

function PickRow({ pick, idx }) {
  return (
    <div className="flex items-start gap-2 py-1.5 border-t border-[var(--border)] first:border-0">
      <span className="text-xs text-[var(--text)] opacity-65 w-4 shrink-0 pt-0.5">{idx}.</span>
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-[var(--text-h)] truncate">{pick.fixture}</p>
        <p className="text-[10px] text-[var(--text)] opacity-80 truncate">
          {pick.country ? `${pick.country} · ` : ''}{pick.league}
        </p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="text-xs text-[var(--text)]">{pick.market}</span>
        {pick.probability != null && (
          <span className="text-xs font-semibold text-[var(--accent)]">
            {Math.round(pick.probability * 100)}%
          </span>
        )}
        {pick.confidence && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded border font-semibold ${CONF_BADGE[pick.confidence] || CONF_BADGE.Low}`}>
            {pick.confidence}
          </span>
        )}
      </div>
    </div>
  )
}

function ChannelCard({ channel }) {
  const [open, setOpen] = useState(false)
  const colorClass = PROFILE_COLOR[channel.profile] || 'text-[var(--text)]'

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-[var(--code-bg)] transition-colors text-left"
      >
        <span className="text-lg leading-none">{channel.emoji}</span>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${colorClass}`}>{channel.label}</p>
          <p className="text-[10px] text-[var(--text)] opacity-70 font-mono truncate">{channel.chat_id}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm font-bold text-[var(--text-h)]">{channel.pick_count}</span>
          <span className="text-xs text-[var(--text)] opacity-80">picks</span>
          {open
            ? <ChevronDown size={13} className="text-[var(--text)] opacity-70" />
            : <ChevronRight size={13} className="text-[var(--text)] opacity-70" />
          }
        </div>
      </button>
      {open && channel.picks.length > 0 && (
        <div className="px-4 pb-3">
          <p className="text-[10px] text-[var(--text)] opacity-70 mb-1">{channel.subtitle}</p>
          {channel.picks.map((pick, i) => (
            <PickRow key={i} pick={pick} idx={i + 1} />
          ))}
          {channel.pick_count > channel.picks.length && (
            <p className="text-[10px] text-[var(--text)] opacity-65 pt-2 border-t border-[var(--border)]">
              + {channel.pick_count - channel.picks.length} more not shown
            </p>
          )}
        </div>
      )}
      {open && channel.picks.length === 0 && (
        <p className="px-4 pb-3 text-xs text-[var(--text)] opacity-70 italic">No picks match this profile today.</p>
      )}
    </div>
  )
}

// Return today's date as YYYY-MM-DD in local time
function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function TelegramPanel() {
  const [preview, setPreview]   = useState(null)
  const [previewDate, setPreviewDate] = useState(null)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [pushing, setPushing]   = useState(false)
  const [testing, setTesting]   = useState(false)
  const [testResults, setTestResults] = useState(null)
  const [pushResult, setPushResult]   = useState(null)
  const [error, setError]       = useState(null)

  // Results push state
  const [resultsDate, setResultsDate]     = useState(todayStr())
  const [pushingResults, setPushingResults] = useState(false)
  const [resultsResult, setResultsResult]   = useState(null)

  const loadPreview = useCallback(async () => {
    setLoadingPreview(true)
    setError(null)
    try {
      const data = await fetchTelegramPreview()
      setPreview(data.channels)
      setPreviewDate(data.date)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingPreview(false)
    }
  }, [])

  async function handlePush() {
    if (!confirm('Push signals to all configured Telegram channels now?')) return
    setPushing(true)
    setPushResult(null)
    setError(null)
    try {
      const data = await pushTelegramSignals()
      setPushResult(data.sent)
    } catch (e) {
      setError(e.message)
    } finally {
      setPushing(false)
    }
  }

  async function handleTest() {
    setTesting(true)
    setTestResults(null)
    setError(null)
    try {
      const data = await testTelegramSetup()
      setTestResults(data.results)
    } catch (e) {
      setError(e.message)
    } finally {
      setTesting(false)
    }
  }

  async function handlePushResults() {
    if (!confirm(`Push results for ${resultsDate} to all Telegram channels?`)) return
    setPushingResults(true)
    setResultsResult(null)
    setError(null)
    try {
      const data = await pushTelegramResults(resultsDate)
      setResultsResult({ sent: data.sent, date: data.date })
    } catch (e) {
      setError(e.message)
    } finally {
      setPushingResults(false)
    }
  }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--code-bg)] p-5 space-y-4">
      {/* Header + action row */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-h)]">Telegram Channels</h2>
          {previewDate && (
            <p className="text-[10px] text-[var(--text)] opacity-70 mt-0.5">Preview for {previewDate}</p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={loadPreview}
            disabled={loadingPreview}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-xs text-[var(--text)] hover:text-[var(--accent)] transition-colors disabled:opacity-50"
          >
            <RefreshCw size={11} className={loadingPreview ? 'animate-spin' : ''} />
            {preview ? 'Refresh Preview' : 'Load Preview'}
          </button>
          <button
            onClick={handleTest}
            disabled={testing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-xs text-[var(--text)] hover:text-[var(--accent)] transition-colors disabled:opacity-50"
          >
            {testing ? <RefreshCw size={11} className="animate-spin" /> : <CheckCircle size={11} />}
            Test Connection
          </button>
          <button
            onClick={handlePush}
            disabled={pushing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent)] text-white text-xs font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {pushing ? <RefreshCw size={11} className="animate-spin" /> : <Send size={11} />}
            Push Signals Now
          </button>
        </div>
      </div>

      {/* Results push row */}
      <div className="flex items-center gap-2 flex-wrap pt-1 border-t border-[var(--border)]">
        <span className="text-xs text-[var(--text)] opacity-70 shrink-0">Push results for</span>
        <input
          type="date"
          value={resultsDate}
          onChange={e => { setResultsDate(e.target.value); setResultsResult(null) }}
          className="px-2 py-1 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs text-[var(--text)] focus:outline-none focus:border-[var(--accent)]"
        />
        <button
          onClick={handlePushResults}
          disabled={pushingResults || !resultsDate}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--accent)] text-[var(--accent)] text-xs font-medium hover:bg-[var(--accent)] hover:text-white transition-colors disabled:opacity-50"
        >
          {pushingResults
            ? <RefreshCw size={11} className="animate-spin" />
            : <BarChart2 size={11} />
          }
          Push Results
        </button>
      </div>

      {/* Results push feedback */}
      {resultsResult != null && (
        <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs border ${resultsResult.sent ? 'bg-green-500/10 border-green-500/20 text-green-400' : 'bg-amber-500/10 border-amber-500/20 text-amber-400'}`}>
          {resultsResult.sent ? <CheckCircle size={12} /> : <XCircle size={12} />}
          {resultsResult.sent
            ? `Results for ${resultsResult.date} sent to all channels.`
            : `Nothing sent for ${resultsResult.date} — no signals found or channels not configured.`
          }
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-400">
          <XCircle size={12} />
          {error}
        </div>
      )}

      {/* Push result */}
      {pushResult != null && (
        <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs border ${pushResult ? 'bg-green-500/10 border-green-500/20 text-green-400' : 'bg-amber-500/10 border-amber-500/20 text-amber-400'}`}>
          {pushResult ? <CheckCircle size={12} /> : <XCircle size={12} />}
          {pushResult ? 'Signals sent to all channels.' : 'Nothing sent — no channels configured or no picks matched.'}
        </div>
      )}

      {/* Test results */}
      {testResults && (
        <div className="rounded-lg border border-[var(--border)] overflow-hidden">
          {testResults.map(r => (
            <div key={r.label} className="flex items-center gap-3 px-3 py-2 border-b border-[var(--border)] last:border-0 text-xs">
              {r.sent
                ? <CheckCircle size={12} className="text-green-400 shrink-0" />
                : <XCircle size={12} className="text-red-400 shrink-0" />
              }
              <span className={`font-medium w-24 ${PROFILE_COLOR[r.profile] || ''}`}>{r.label}</span>
              <span className="font-mono text-[var(--text)] opacity-80 truncate">{r.chat_id}</span>
              <span className={`ml-auto ${r.sent ? 'text-green-400' : 'text-red-400'}`}>
                {r.sent ? 'OK' : 'FAILED'}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Channel previews */}
      {preview === null && !loadingPreview && (
        <p className="text-xs text-[var(--text)] opacity-70 italic text-center py-3">
          Click "Load Preview" to see what each channel would receive today.
        </p>
      )}
      {preview && preview.length === 0 && (
        <p className="text-xs text-[var(--text)] opacity-70 italic text-center py-3">
          No Telegram channels are configured. Add chat IDs to backend/.env and restart the server.
        </p>
      )}
      {preview && preview.length > 0 && (
        <div className="space-y-2">
          {preview.map(ch => (
            <ChannelCard key={ch.chat_id} channel={ch} />
          ))}
        </div>
      )}
    </div>
  )
}
