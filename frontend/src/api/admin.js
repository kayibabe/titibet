import { apiFetch } from './client'

export async function fetchAdminStats() {
  const res = await apiFetch('/api/admin/stats')
  if (!res.ok) throw new Error('Failed to load stats')
  return res.json()
}

export async function fetchUsers({ search, tier, status } = {}) {
  const params = new URLSearchParams()
  if (search) params.set('search', search)
  if (tier) params.set('tier', tier)
  if (status) params.set('status', status)
  const res = await apiFetch(`/api/admin/users?${params}`)
  if (!res.ok) throw new Error('Failed to load users')
  return res.json()
}

export async function updateUser(userId, patch) {
  const res = await apiFetch(`/api/admin/users/${userId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Update failed')
  }
  return res.json()
}

export async function triggerAdminSettle() {
  const res = await apiFetch('/api/admin/settle', { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Settle failed: ${res.status}`)
  }
  return res.json()   // { settled: number }
}

export async function deactivateUser(userId) {
  const res = await apiFetch(`/api/admin/users/${userId}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Deactivate failed')
  }
  return res.json()
}

/** Returns Telegram config + recent getUpdates so you can find the group chat ID. */
export async function fetchTelegramStatus() {
  const res = await apiFetch('/api/admin/telegram/status')
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to fetch Telegram status')
  }
  return res.json()
}

/** Sends a test message to every configured Telegram chat. */
export async function testTelegramSetup() {
  const res = await apiFetch('/api/admin/telegram/test', { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Telegram test failed')
  }
  return res.json()   // { results: [{ label, chat_id, profile, sent }] }
}

/** Preview what each channel would receive without sending. */
export async function fetchTelegramPreview() {
  const res = await apiFetch('/api/admin/telegram/preview')
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to fetch Telegram preview')
  }
  return res.json()   // { date, channels: [{ label, emoji, profile, chat_id, pick_count, picks }] }
}

/** Push today's signals to all configured Telegram channels immediately. */
export async function pushTelegramSignals() {
  const res = await apiFetch('/api/admin/telegram/push-digest', { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Telegram push failed')
  }
  return res.json()   // { sent: bool, date: string }
}

/**
 * Push a results digest for a given date (YYYY-MM-DD) to all configured channels.
 * Uses force=True on the backend — sends even if results were already sent.
 * Defaults to today when dateStr is omitted.
 */
export async function pushTelegramResults(dateStr) {
  const params = dateStr ? `?date=${encodeURIComponent(dateStr)}` : ''
  const res = await apiFetch(`/api/admin/telegram/push-results${params}`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Results push failed')
  }
  return res.json()   // { sent: bool, date: string }
}

/** Get current API-Football quota snapshot. */
export async function fetchApiQuota() {
  const res = await apiFetch('/api/admin/quota')
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to fetch quota')
  }
  return res.json()   // { limit, remaining, pct_used, reset_note, date }
}

/** List learning proposals from self-learning pipelines. */
export async function fetchLearningProposals({ activeOnly = true } = {}) {
  const res = await apiFetch(`/api/admin/learning-proposals?active_only=${activeOnly}`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to fetch proposals')
  }
  return res.json()   // [{ id, change_type, target, proposed_value, rationale, confidence, backtest_note, is_active, created_at }]
}

/** Manually deactivate (override) a learning proposal. */
export async function deactivateLearningProposal(id) {
  const res = await apiFetch(`/api/admin/learning-proposals/${id}/deactivate`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Deactivate failed')
  }
  return res.json()
}

/** Manually trigger Pipeline A — Loss Analysis. */
export async function triggerLossAnalysisPipeline() {
  const res = await apiFetch('/api/admin/pipelines/loss-analysis', { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Pipeline A failed')
  }
  return res.json()
}

/** Manually trigger Pipeline B — Strategy. */
export async function triggerStrategyPipeline() {
  const res = await apiFetch('/api/admin/pipelines/strategy', { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Pipeline B failed')
  }
  return res.json()
}
