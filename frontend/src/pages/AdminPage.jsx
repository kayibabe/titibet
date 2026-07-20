import { useState, useEffect, useCallback } from 'react'
import { Search, RefreshCw, Shield, Users, CheckCircle, ChevronDown, Trash2, CalendarRange, Play, Eye } from 'lucide-react'
import { fetchAdminStats, fetchUsers, updateUser, deactivateUser, cleanupTrackedBets, backfillDates } from '../api/admin'
import { fmtDate, fmtDateTime } from '../utils/format'
import TelegramPanel from '../components/admin/TelegramPanel'
import QuotaWidget from '../components/admin/QuotaWidget'
import LearningProposalsPanel from '../components/admin/LearningProposalsPanel'

const TIER_OPTIONS = ['free', 'pro']
const STATUS_OPTIONS = ['inactive', 'active', 'cancelled', 'past_due']

const TIER_STYLE = {
  free:  'bg-slate-500/15 text-slate-400 border-slate-500/30',
  pro: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
}
const STATUS_STYLE = {
  active:     'bg-green-500/15 text-green-400 border-green-500/30',
  inactive:   'bg-slate-500/15 text-slate-400 border-slate-500/30',
  cancelled:  'bg-red-500/15 text-red-400 border-red-500/30',
  past_due:   'bg-amber-500/15 text-amber-400 border-amber-500/30',
}

function Pill({ value, styles }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold border tracking-wide ${styles[value] || ''}`}>
      {value}
    </span>
  )
}

function StatCard({ label, value, icon: Icon, color }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] px-4 py-3 flex items-center gap-3">
      <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${color}`}>
        <Icon size={16} className="text-white" />
      </div>
      <div>
        <p className="text-xs text-[var(--text)] opacity-75">{label}</p>
        <p className="text-xl font-bold text-[var(--text-h)]">{value}</p>
      </div>
    </div>
  )
}

function EditableCell({ value, options, onSave }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1 hover:opacity-80 transition-opacity"
      >
        <Pill value={value} styles={{ ...TIER_STYLE, ...STATUS_STYLE }} />
        <ChevronDown size={10} className="text-[var(--text)] opacity-70" />
      </button>
      {open && (
        <div className="absolute z-50 top-full mt-1 left-0 bg-[var(--bg)] border border-[var(--border)] rounded-lg shadow-lg py-1 min-w-[120px]">
          {options.map(opt => (
            <button
              key={opt}
              onClick={() => { onSave(opt); setOpen(false) }}
              className="w-full text-left px-3 py-1.5 text-xs text-[var(--text)] hover:bg-[var(--code-bg)] hover:text-[var(--text-h)]"
            >
              {opt}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function SectionHeader({ icon: Icon, title, subtitle }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <div className="w-8 h-8 rounded-lg bg-[var(--accent)]/15 flex items-center justify-center shrink-0">
        <Icon size={15} className="text-[var(--accent)]" />
      </div>
      <div>
        <h3 className="text-sm font-semibold text-[var(--text-h)]">{title}</h3>
        {subtitle && <p className="text-xs text-[var(--text)] opacity-65">{subtitle}</p>}
      </div>
    </div>
  )
}

function CleanupPanel() {
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function run(dryRun) {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const data = await cleanupTrackedBets({ dryRun })
      setResult({ ...data, dryRun })
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const betTotal = result?.tracked_bets_deleted?.total ?? 0
  const sigTotal = result?.signal_rows_deleted?.total ?? 0
  const overall  = result?.post_cleanup_analytics?.overall

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4">
      <SectionHeader icon={Trash2} title="DB Cleanup" subtitle="Remove tracked bets & signals that violate current gate rules (disabled leagues, Bayesian Only Over 1.5, odds floors, etc.)" />

      <div className="flex gap-2 mb-4">
        <button
          onClick={() => run(true)}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-[var(--border)] text-xs font-medium text-[var(--text)] hover:text-[var(--text-h)] disabled:opacity-50 transition-colors"
        >
          <Eye size={13} />
          Preview
        </button>
        <button
          onClick={() => run(false)}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-red-600/80 hover:bg-red-600 text-xs font-medium text-white disabled:opacity-50 transition-colors"
        >
          {loading ? <RefreshCw size={13} className="animate-spin" /> : <Trash2 size={13} />}
          Execute Cleanup
        </button>
      </div>

      {error && <p className="text-xs text-red-400 mb-3">{error}</p>}

      {result && (
        <div className="space-y-3 text-xs">
          <div className={`px-3 py-2 rounded-lg font-medium ${result.dryRun ? 'bg-amber-500/10 text-amber-400' : 'bg-green-500/10 text-green-400'}`}>
            {result.dryRun ? '⚠ Preview mode — no changes made' : '✓ Cleanup executed'}
            {' · '}{betTotal} bet row{betTotal !== 1 ? 's' : ''} + {sigTotal} signal row{sigTotal !== 1 ? 's' : ''} {result.dryRun ? 'would be deleted' : 'deleted'}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <p className="text-[10px] font-semibold text-[var(--text)] opacity-60 mb-1">TRACKED BETS</p>
              {Object.entries(result.tracked_bets_deleted || {}).filter(([k]) => k !== 'total').map(([k, v]) => v > 0 && (
                <div key={k} className="flex justify-between text-[var(--text)] py-0.5">
                  <span className="opacity-70 capitalize">{k.replace(/_/g,' ')}</span>
                  <span className="font-semibold text-red-400">{v}</span>
                </div>
              ))}
            </div>
            <div>
              <p className="text-[10px] font-semibold text-[var(--text)] opacity-60 mb-1">SIGNAL ROWS</p>
              {Object.entries(result.signal_rows_deleted || {}).filter(([k]) => k !== 'total').map(([k, v]) => v > 0 && (
                <div key={k} className="flex justify-between text-[var(--text)] py-0.5">
                  <span className="opacity-70 capitalize">{k.replace(/_/g,' ')}</span>
                  <span className="font-semibold text-red-400">{v}</span>
                </div>
              ))}
            </div>
          </div>

          {!result.dryRun && overall && (
            <div className="rounded-lg bg-[var(--code-bg)] px-3 py-2">
              <p className="text-[10px] font-semibold text-[var(--text)] opacity-60 mb-1">POST-CLEANUP WIN RATE</p>
              <p className="text-[var(--text-h)] font-bold text-sm">
                {overall.win_rate_pct ?? '—'}%
                <span className="text-xs font-normal text-[var(--text)] opacity-70 ml-2">
                  {overall.won}W / {overall.lost}L · ROI {overall.roi_pct ?? '—'}%
                </span>
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function BackfillPanel() {
  const today = new Date().toISOString().slice(0, 10)
  const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10)
  const [dateFrom, setDateFrom] = useState(sevenDaysAgo)
  const [dateTo, setDateTo]     = useState(today)
  const [result, setResult]     = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)

  async function run(dryRun) {
    if (!dateFrom) { setError('Start date required'); return }
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const data = await backfillDates({ dateFrom, dateTo: dateTo || today, dryRun })
      setResult({ ...data, dryRun })
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const ACTION_COLOR = {
    'sync+compute+track': 'text-amber-400',
    'compute+track':      'text-blue-400',
    'track':              'text-green-400',
    'skip (already tracked)': 'text-[var(--text)] opacity-40',
  }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4">
      <SectionHeader icon={CalendarRange} title="Backfill Dates" subtitle="Ingest + recompute signals + auto-track for missing dates. Past dates return scores but not odds — signals recompute from existing market snapshots." />

      <div className="flex flex-wrap gap-2 mb-4 items-end">
        <div>
          <label className="block text-[10px] text-[var(--text)] opacity-60 mb-1">FROM</label>
          <input
            type="date"
            value={dateFrom}
            onChange={e => setDateFrom(e.target.value)}
            className="px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-xs focus:outline-none focus:border-[var(--accent)]"
          />
        </div>
        <div>
          <label className="block text-[10px] text-[var(--text)] opacity-60 mb-1">TO</label>
          <input
            type="date"
            value={dateTo}
            onChange={e => setDateTo(e.target.value)}
            className="px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-xs focus:outline-none focus:border-[var(--accent)]"
          />
        </div>
        <button
          onClick={() => run(true)}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-[var(--border)] text-xs font-medium text-[var(--text)] hover:text-[var(--text-h)] disabled:opacity-50 transition-colors"
        >
          <Eye size={13} />
          Preview
        </button>
        <button
          onClick={() => run(false)}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent)]/80 text-xs font-medium text-white disabled:opacity-50 transition-colors"
        >
          {loading ? <RefreshCw size={13} className="animate-spin" /> : <Play size={13} />}
          Run Backfill
        </button>
      </div>

      {error && <p className="text-xs text-red-400 mb-3">{error}</p>}

      {result && (
        <div className="space-y-3 text-xs">
          {result.dryRun ? (
            <>
              <div className="px-3 py-2 rounded-lg bg-amber-500/10 text-amber-400 font-medium">
                ⚠ Preview — {result.preview?.length} date(s) in range
              </div>
              <div className="rounded-lg border border-[var(--border)] overflow-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-[var(--code-bg)] border-b border-[var(--border)]">
                      <th className="px-3 py-2 text-left text-[var(--text)] opacity-60">Date</th>
                      <th className="px-3 py-2 text-right text-[var(--text)] opacity-60">Snapshots</th>
                      <th className="px-3 py-2 text-right text-[var(--text)] opacity-60">Signals</th>
                      <th className="px-3 py-2 text-right text-[var(--text)] opacity-60">Bets</th>
                      <th className="px-3 py-2 text-left text-[var(--text)] opacity-60">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.preview?.map(row => (
                      <tr key={row.date} className="border-t border-[var(--border)]">
                        <td className="px-3 py-1.5 text-[var(--text-h)]">{row.date}</td>
                        <td className="px-3 py-1.5 text-right text-[var(--text)] opacity-75">{row.market_snapshots}</td>
                        <td className="px-3 py-1.5 text-right text-[var(--text)] opacity-75">{row.signals}</td>
                        <td className="px-3 py-1.5 text-right text-[var(--text)] opacity-75">{row.system_bets}</td>
                        <td className={`px-3 py-1.5 ${ACTION_COLOR[row.action] || ''}`}>{row.action}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <>
              <div className="px-3 py-2 rounded-lg bg-green-500/10 text-green-400 font-medium">
                ✓ Backfill complete · {result.days_processed} date(s) · {result.total_bets_tracked} new bets tracked · {result.total_settled} settled
              </div>
              <div className="space-y-1">
                {result.detail?.map(row => (
                  <div key={row.date} className="flex gap-3 items-start text-[var(--text)]">
                    <span className="text-[var(--text-h)] font-medium w-24 shrink-0">{row.date}</span>
                    {row.error
                      ? <span className="text-red-400">{row.error}</span>
                      : <span className="opacity-70">
                          {row.ingested ? `✓ ingested ${row.fixtures_pulled ?? 0} fixtures` : '— no ingest'} ·{' '}
                          {row.signals_computed} signals · {row.bets_tracked} bets tracked
                        </span>
                    }
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function AdminPage() {
  const [stats, setStats] = useState(null)
  const [users, setUsers] = useState([])
  const [search, setSearch] = useState('')
  const [filterTier, setFilterTier] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(null) // user id being saved

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, u] = await Promise.all([
        fetchAdminStats(),
        fetchUsers({ search, tier: filterTier, status: filterStatus }),
      ])
      setStats(s)
      setUsers(u)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [search, filterTier, filterStatus])

  useEffect(() => { load() }, [load])

  async function handleUpdate(userId, patch) {
    setSaving(userId)
    try {
      const updated = await updateUser(userId, patch)
      setUsers(prev => prev.map(u => u.id === userId ? updated : u))
      // Refresh stats
      fetchAdminStats().then(setStats).catch(() => {})
    } catch (e) {
      alert(e.message)
    } finally {
      setSaving(null)
    }
  }

  async function handleDeactivate(userId, name) {
    if (!confirm(`Deactivate ${name || userId}? They will be logged out.`)) return
    setSaving(userId)
    try {
      await deactivateUser(userId)
      setUsers(prev => prev.map(u => u.id === userId ? { ...u, is_active: false } : u))
    } catch (e) {
      alert(e.message)
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="space-y-6">
      {/* Stats row */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatCard label="Total Users" value={stats.total_users} icon={Users} color="bg-[var(--accent)]" />
          <StatCard label="Active Subs" value={stats.active_subscriptions} icon={CheckCircle} color="bg-green-600" />
          <StatCard label="Pro" value={stats.pro_users} icon={Shield} color="bg-blue-600" />
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="relative flex-1 sm:max-w-xs">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text)] opacity-70" />
          <input
            type="text"
            placeholder="Search email or name…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-8 pr-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]"
          />
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={filterTier}
            onChange={e => setFilterTier(e.target.value)}
            className="flex-1 sm:flex-none px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]"
          >
            <option value="">All tiers</option>
            {TIER_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            className="flex-1 sm:flex-none px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]"
          >
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <button
            onClick={load}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-[var(--border)] text-[var(--text)] text-sm hover:text-[var(--accent)] transition-colors"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Users — mobile cards */}
      {users.length === 0 && (
        <p className="text-center py-8 text-sm text-[var(--text)] opacity-70">
          {loading ? 'Loading…' : 'No users found'}
        </p>
      )}
      <div className="sm:hidden space-y-3">
        {users.map(u => (
          <div key={u.id} className={`rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4 space-y-3 ${saving === u.id ? 'opacity-70' : ''}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-semibold text-sm text-[var(--text-h)] truncate">{u.name || '—'}</p>
                <p className="text-xs text-[var(--text)] opacity-65 truncate">{u.email}</p>
              </div>
              {u.is_active
                ? <span className="text-[10px] font-semibold text-green-400 shrink-0">Active</span>
                : <span className="text-[10px] font-semibold text-red-400 shrink-0">Deactivated</span>
              }
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <EditableCell value={u.tier} options={TIER_OPTIONS} onSave={v => handleUpdate(u.id, { tier: v })} />
              <EditableCell value={u.subscription_status} options={STATUS_OPTIONS} onSave={v => handleUpdate(u.id, { subscription_status: v })} />
              <button
                title={u.is_admin ? 'Revoke admin' : 'Grant admin'}
                onClick={() => handleUpdate(u.id, { is_admin: !u.is_admin })}
                className={`flex items-center gap-1 px-2 py-1 rounded-md border text-[10px] font-semibold transition-colors ${
                  u.is_admin
                    ? 'border-amber-500/50 bg-amber-500/15 text-amber-400'
                    : 'border-[var(--border)] text-[var(--text)] opacity-40 hover:opacity-80'
                }`}
              >
                <Shield size={10} />
                {u.is_admin ? 'Admin' : 'Admin'}
              </button>
              <span className="text-xs text-[var(--text)] opacity-80 ml-auto">{fmtDate(u.created_at)}</span>
            </div>
            <div className="flex items-center gap-1 text-[11px] text-[var(--text)] opacity-60">
              <span>Last active:</span>
              <span className="font-medium opacity-90">
                {u.last_active_at ? fmtDateTime(u.last_active_at) : 'Never'}
              </span>
            </div>
            {u.is_active && (
              <button
                onClick={() => handleDeactivate(u.id, u.name || u.email)}
                className="text-xs text-red-400 hover:underline"
              >
                Deactivate
              </button>
            )}
          </div>
        ))}
      </div>

      {/* System operations — cleanup + backfill */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <CleanupPanel />
        <BackfillPanel />
      </div>

      {/* System health — quota + learning proposals side by side on desktop */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="lg:col-span-1">
          <QuotaWidget />
        </div>
        <div className="lg:col-span-2">
          <LearningProposalsPanel />
        </div>
      </div>

      {/* Telegram panel */}
      <TelegramPanel />

      {/* Users — desktop table */}
      <div className="hidden sm:block rounded-xl border border-[var(--border)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] bg-[var(--code-bg)]">
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--text)] opacity-75">User</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--text)] opacity-75">Tier</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--text)] opacity-75">Subscription</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--text)] opacity-75">Joined</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--text)] opacity-75">Last Active</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--text)] opacity-75">Status</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--text)] opacity-75">Admin</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id} className={`border-t border-[var(--border)] hover:bg-[var(--code-bg)] transition-colors ${saving === u.id ? 'opacity-70' : ''}`}>
                  <td className="px-4 py-3">
                    <p className="font-medium text-[var(--text-h)]">{u.name || '—'}</p>
                    <p className="text-xs text-[var(--text)] opacity-65">{u.email}</p>
                  </td>
                  <td className="px-4 py-3">
                    <EditableCell value={u.tier} options={TIER_OPTIONS} onSave={v => handleUpdate(u.id, { tier: v })} />
                  </td>
                  <td className="px-4 py-3">
                    <EditableCell value={u.subscription_status} options={STATUS_OPTIONS} onSave={v => handleUpdate(u.id, { subscription_status: v })} />
                  </td>
                  <td className="px-4 py-3 text-xs text-[var(--text)] opacity-75">{fmtDate(u.created_at)}</td>
                  <td className="px-4 py-3 text-xs text-[var(--text)] opacity-75">
                    {u.last_active_at ? fmtDateTime(u.last_active_at) : <span className="opacity-40">Never</span>}
                  </td>
                  <td className="px-4 py-3">
                    {u.is_active
                      ? <span className="text-xs text-green-400">Active</span>
                      : <span className="text-xs text-red-400">Deactivated</span>
                    }
                  </td>
                  <td className="px-4 py-3">
                    <button
                      title={u.is_admin ? 'Revoke admin' : 'Grant admin'}
                      onClick={() => handleUpdate(u.id, { is_admin: !u.is_admin })}
                      className={`flex items-center justify-center w-7 h-7 rounded-md border transition-colors ${
                        u.is_admin
                          ? 'border-amber-500/50 bg-amber-500/15 text-amber-400 hover:bg-amber-500/25'
                          : 'border-[var(--border)] text-[var(--text)] opacity-40 hover:opacity-80 hover:border-amber-500/40 hover:text-amber-400'
                      }`}
                    >
                      <Shield size={12} />
                    </button>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {u.is_active && (
                      <button onClick={() => handleDeactivate(u.id, u.name || u.email)} className="text-xs text-[var(--text)] opacity-70 hover:text-red-400 hover:opacity-100 transition-colors">
                        Deactivate
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
