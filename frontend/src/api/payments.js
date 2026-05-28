import { apiFetch } from './client'

export async function fetchPlans() {
  const res = await apiFetch('/api/payments/plans')
  if (!res.ok) throw new Error('Failed to load plans')
  return res.json()
}

export async function initializePayment(planId) {
  const res = await apiFetch('/api/payments/initialize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan_id: planId }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Payment initialization failed')
  }
  return res.json()
}

export async function verifyPayment(reference) {
  const res = await apiFetch(`/api/payments/verify?reference=${reference}`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Verification failed')
  }
  return res.json()
}
