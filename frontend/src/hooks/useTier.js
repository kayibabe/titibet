/**
 * useTier — derive subscription capabilities from the logged-in user.
 *
 * Usage:
 *   const { tier, isPro, canAccess, isActive } = useTier()
 *   if (!canAccess('pro')) return <UpgradePrompt required="pro" />
 */

import { useAuth } from '../context/AuthContext'

export const TIER_RANK = { free: 0, pro: 1 }

export default function useTier() {
  const { user } = useAuth()

  const tier     = user?.tier ?? 'free'
  const status   = user?.subscription_status ?? 'inactive'
  // A subscription is "active" when Paystack marks it so, OR the user is free
  // (free has no subscription_status but is always usable).
  const isActive = tier === 'free' || status === 'active'

  const isPro = isActive && tier === 'pro'

  /**
   * Returns true when the current user meets or exceeds the required tier.
   * @param {'pro'} required
   */
  function canAccess(required) {
    if (!required || required === 'free') return true
    return (TIER_RANK[tier] ?? 0) >= (TIER_RANK[required] ?? 1) && isActive
  }

  return { tier, isActive, isPro, canAccess }
}
