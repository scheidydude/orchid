import { useState, useEffect, useCallback, useRef } from 'react'

export function useProjects() {
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [newProjectIds, setNewProjectIds] = useState(new Set())
  const prevIdsRef = useRef(new Set())

  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetch('/api/projects')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()

      // Track newly discovered projects
      const currentIds = new Set(data.map(p => p.id))
      const prevIds = prevIdsRef.current
      if (prevIds.size > 0) {
        const added = new Set([...currentIds].filter(id => !prevIds.has(id)))
        if (added.size > 0) {
          setNewProjectIds(prev => new Set([...prev, ...added]))
          // Clear badge after 10 seconds
          setTimeout(() => {
            setNewProjectIds(prev => {
              const next = new Set(prev)
              added.forEach(id => next.delete(id))
              return next
            })
          }, 10000)
        }
      }
      prevIdsRef.current = currentIds

      setProjects(data)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchProjects()
    const interval = setInterval(fetchProjects, 30000)
    return () => clearInterval(interval)
  }, [fetchProjects])

  return { projects, loading, error, refresh: fetchProjects, newProjectIds }
}
