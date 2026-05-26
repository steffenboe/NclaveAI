import { createContext, useContext, useState, useEffect, useCallback } from 'react'

const AuthContext = createContext(null)

/**
 * Provides `user` (undefined = loading, null = not authenticated, object = current user),
 * `setUser`, and `logout()` to the component tree.
 *
 * Also listens for the global 'auth:unauthorized' event dispatched by apiFetch
 * to automatically clear the session when any API call returns 401.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined)

  // Check existing session on mount
  useEffect(() => {
    fetch('/api/auth/me')
      .then(res => (res.ok ? res.json() : null))
      .then(data => setUser(data))
      .catch(() => setUser(null))
  }, [])

  const logout = useCallback(async () => {
    try { await fetch('/api/auth/logout', { method: 'POST' }) } catch {}
    setUser(null)
  }, [])

  // React to 401 events dispatched anywhere via apiFetch
  useEffect(() => {
    const handle = () => setUser(null)
    window.addEventListener('auth:unauthorized', handle)
    return () => window.removeEventListener('auth:unauthorized', handle)
  }, [])

  return (
    <AuthContext.Provider value={{ user, setUser, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
