import { useState, useEffect, useCallback } from 'react'

export function useAuth() {
  const [user, setUser]     = useState(null)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    fetch('/api/auth/me')
      .then(r => r.ok ? r.json() : null)
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
