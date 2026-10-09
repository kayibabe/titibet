import { createContext, useContext } from 'react'

const AuthContext = createContext(null)

// The local checkout deliberately has no login or credential flow. The backend
// supplies the same detached identity for local development requests.
const LOCAL_USER = {
  id: 0,
  email: 'local-admin@localhost',
  name: 'Local Admin',
  tier: 'elite',
  subscription_status: 'active',
  subscription_expires_at: '2099-12-31T00:00:00Z',
  timezone: 'Africa/Blantyre',
  is_admin: true,
}

export function AuthProvider({ children }) {
  return (
    <AuthContext.Provider value={{
      token: null,
      user: LOCAL_USER,
      loading: false,
      logout: () => {},
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
