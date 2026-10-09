import { useState } from 'react'
import { Save, User, CheckCircle } from 'lucide-react'
import { useAuth } from '../context/AuthContext'

const TIMEZONES = ['Africa/Blantyre', 'Africa/Nairobi', 'Africa/Johannesburg', 'UTC']

export default function AccountPage() {
  const { user } = useAuth()
  const [name, setName] = useState(user?.name || '')
  const [timezone, setTimezone] = useState(user?.timezone || 'Africa/Blantyre')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  async function saveProfile(event) {
    event.preventDefault()
    setError('')
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="space-y-6 max-w-xl">
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] px-5 py-4 flex items-center gap-4">
        <div className="w-10 h-10 rounded-full bg-[var(--accent-bg)] flex items-center justify-center"><User size={18} className="text-[var(--accent)]" /></div>
        <div><p className="text-sm font-semibold text-[var(--text-h)]">{user?.email}</p><p className="text-xs text-violet-400 font-semibold">Elite local access</p></div>
      </div>
      <section className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5 space-y-4">
        <h2 className="text-sm font-semibold text-[var(--text-h)]">Local profile</h2>
        <form onSubmit={saveProfile} className="space-y-4">
          <label className="block text-sm text-[var(--text-h)]">Display name<input value={name} onChange={e => setName(e.target.value)} className="mt-1 w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)]" /></label>
          <label className="block text-sm text-[var(--text-h)]">Timezone<select value={timezone} onChange={e => setTimezone(e.target.value)} className="mt-1 w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] text-[var(--text-h)]">{TIMEZONES.map(tz => <option key={tz}>{tz}</option>)}</select></label>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <button type="submit" className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-semibold">{saved ? <CheckCircle size={14} /> : <Save size={14} />}{saved ? 'Saved!' : 'Save Profile'}</button>
        </form>
      </section>
    </div>
  )
}
