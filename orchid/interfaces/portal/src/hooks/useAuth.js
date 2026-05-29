import { useState, useEffect, useCallback } from 'react'

export function useAuth() {
  const [user, setUser] = useState(null)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    fetch('/api/auth/me')
      .then(async r => {
        if (r.ok) return r.json()
        if (r.status === 401) {
          // Access token expired — try silent refresh via refresh cookie
          const ref = await fetch('/api/auth/refresh', { method: 'POST' }).catch(() => null)
          if (ref?.ok) {
            const r2 = await fetch('/api/auth/me').catch(() => null)
            return r2?.ok ? r2.json() : null
          }
        }
        return null
      })
      .then(d => { if (d?.authenticated) setUser(d) })
      .catch(() => {})
      .finally(() => setChecked(true))
  }, [])

  const logout = useCallback(async () => {
    await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {})
    setUser(null)
  }, [])

  return { user, checked, setUser, logout }
}
