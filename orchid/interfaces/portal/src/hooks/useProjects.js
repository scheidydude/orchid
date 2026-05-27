import { useState, useEffect, useCallback } from 'react'

export function useProjects() {
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    setLoading(true)
    fetch('/api/projects')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { setProjects(d.projects || []); setError(null) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  return { projects, loading, error, refresh }
}
