import { createContext, useContext, useState, useEffect, useCallback } from 'react'

const AuthContext = createContext(null)

const TOKEN_KEY = 'titibet_token'
const API_BASE = import.meta.env.VITE_API_BASE || ''

function normalizeApiError(payload, fallback) {
  if (!payload) return fallback
  if (typeof payload === 'string') return payload

  if (Array.isArray(payload.detail)) {
    const messages = payload.detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item?.msg) return item.msg
        return null
      })
      .filter(Boolean)

    if (messages.length) {
      return messages.join('. ')
    }
  }

  if (typeof payload.detail === 'string') return payload.detail
  if (typeof payload.message === 'string') return payload.message

  return fallback
}

async function parseErrorResponse(res, fallback) {
  const payload = await res.json().catch(() => null)
  if (res.status === 404) {
    return 'TiTiBet auth endpoint is unavailable. Check that the TiTiBet backend is running on the configured API port.'
  }
  return normalizeApiError(payload, fallback)
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(!!localStorage.getItem(TOKEN_KEY))

  const fetchMe = useCallback(async (t) => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/me`, {
        headers: { Authorization: `Bearer ${t}` },
      })
      if (res.ok) {
        const data = await res.json()
        setUser(data)
      } else {
        // Token invalid / expired
        setToken(null)
        setUser(null)
        localStorage.removeItem(TOKEN_KEY)
      }
    } catch {
      setToken(null)
      setUser(null)
      localStorage.removeItem(TOKEN_KEY)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (token) {
      fetchMe(token)
    } else {
      setLoading(false)
    }
  }, [token, fetchMe])

  async function login(email, password) {
    const body = new URLSearchParams({ username: email, password })
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    })
    if (!res.ok) {
      throw new Error(await parseErrorResponse(res, 'Login failed'))
    }
    const data = await res.json()
    localStorage.setItem(TOKEN_KEY, data.access_token)
    setToken(data.access_token)
    await fetchMe(data.access_token)
  }

  async function register(email, password, name) {
    const res = await fetch(`${API_BASE}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, name }),
    })
    if (!res.ok) {
      throw new Error(await parseErrorResponse(res, 'Registration failed'))
    }
    const data = await res.json()
    localStorage.setItem(TOKEN_KEY, data.access_token)
    setToken(data.access_token)
    await fetchMe(data.access_token)
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY)
    setToken(null)
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ token, user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
