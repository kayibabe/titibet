import { useState } from 'react'
import { Save, KeyRound, User, CheckCircle } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { apiFetch } from '../api/client'
import { fmtDate } from '../utils/format'

const TIMEZONES = [
  'Africa/Blantyre', 'Africa/Nairobi', 'Africa/Johannesburg', 'Africa/Lagos',
  'Africa/Cairo', 'Europe/London', 'Europe/Paris', 'Asia/Dubai', 'UTC',
]

function Section({ title, children }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5 space-y-4">
      <h2 className="text-sm font-semibold text-[var(--text-h)]">{title}</h2>
      {children}
    </div>
  )
}

function Field({ label, children }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium text-[var(--text-h)]">{label}</label>
      {children}
    </div>
  )
}

function Input({ value, onChange, type = 'text', placeholder, autoComplete }) {
  return (
    <input
      type={type}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      autoComplete={autoComplete}
      className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
    />
  )
}

export default function AccountPage() {
  const { user, logout } = useAuth()

  const [name, setName] = useState(user?.name || '')
  const [timezone, setTimezone] = useState(user?.timezone || 'Africa/Blantyre')
  const [profileSaved, setProfileSaved] = useState(false)
  const [profileError, setProfileError] = useState('')

  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [pwSaved, setPwSaved] = useState(false)
  const [pwError, setPwError] = useState('')
  const [pwLoading, setPwLoading] = useState(false)

  async function saveProfile(e) {
    e.preventDefault()
    setProfileError('')
    try {
      const res = await apiFetch('/api/auth/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, timezone }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Save failed')
      }
      setProfileSaved(true)
      setTimeout(() => setProfileSaved(false), 2000)
    } catch (e) {
      setProfileError(e.message)
    }
  }

  async function changePassword(e) {
    e.preventDefault()
    setPwError('')
    if (newPw !== confirmPw) { setPwError('Passwords do not match'); return }
    if (newPw.length < 8) { setPwError('Password must be at least 8 characters'); return }
    setPwLoading(true)
    try {
      const res = await apiFetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Change failed')
      }
      setCurrentPw(''); setNewPw(''); setConfirmPw('')
      setPwSaved(true)
      setTimeout(() => setPwSaved(false), 2000)
    } catch (e) {
      setPwError(e.message)
    } finally {
      setPwLoading(false)
    }
  }

  const TIER_STYLE = {
    free:  'text-slate-400',
    pro:   'text-blue-400',
  }

  return (
    <div className="space-y-6 max-w-xl">

      {/* Subscription banner */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] px-5 py-4 flex items-center gap-4 flex-wrap">
        <div className="w-10 h-10 rounded-full bg-[var(--accent-bg)] flex items-center justify-center shrink-0">
          <User size={18} className="text-[var(--accent)]" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-[var(--text-h)] truncate">{user?.email}</p>
          <p className={`text-xs font-semibold capitalize ${TIER_STYLE[user?.tier] || 'text-slate-400'}`}>
            {user?.tier} plan
            {user?.subscription_expires_at && (
              <span className="text-[var(--text)] opacity-80 font-normal ml-1.5">
                · renews {fmtDate(user.subscription_expires_at)}
              </span>
            )}
          </p>
        </div>
      </div>

      {/* Profile */}
      <Section title="Profile">
        <form onSubmit={saveProfile} className="space-y-4">
          <Field label="Display name">
            <Input value={name} onChange={setName} placeholder="Your name" autoComplete="name" />
          </Field>
          <Field label="Timezone">
            <select
              value={timezone}
              onChange={e => setTimezone(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)] text-sm focus:outline-none focus:border-[var(--accent)]"
            >
              {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
            </select>
          </Field>

          {profileError && (
            <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{profileError}</p>
          )}

          <button
            type="submit"
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 transition-opacity"
          >
            {profileSaved ? <CheckCircle size={14} /> : <Save size={14} />}
            {profileSaved ? 'Saved!' : 'Save Profile'}
          </button>
        </form>
      </Section>

      {/* Password */}
      <Section title="Change Password">
        <form onSubmit={changePassword} className="space-y-4">
          <Field label="Current password">
            <Input type="password" value={currentPw} onChange={setCurrentPw} autoComplete="current-password" placeholder="••••••••" />
          </Field>
          <Field label="New password">
            <Input type="password" value={newPw} onChange={setNewPw} autoComplete="new-password" placeholder="At least 8 characters" />
          </Field>
          <Field label="Confirm new password">
            <Input type="password" value={confirmPw} onChange={setConfirmPw} autoComplete="new-password" placeholder="Repeat new password" />
          </Field>

          {pwError && (
            <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{pwError}</p>
          )}

          <button
            type="submit"
            disabled={pwLoading}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {pwSaved ? <CheckCircle size={14} /> : <KeyRound size={14} />}
            {pwSaved ? 'Updated!' : pwLoading ? 'Updating…' : 'Update Password'}
          </button>
        </form>
      </Section>

      {/* Danger zone */}
      <Section title="Session">
        <p className="text-sm text-[var(--text)] opacity-75">Sign out of all devices by logging out here.</p>
        <button
          onClick={logout}
          className="px-4 py-2 rounded-lg border border-red-500/30 text-red-400 text-sm font-semibold hover:bg-red-500/10 transition-colors"
        >
          Sign Out
        </button>
      </Section>
    </div>
  )
}
