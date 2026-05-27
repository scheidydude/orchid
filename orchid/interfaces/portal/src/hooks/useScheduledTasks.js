import { useState, useEffect, useCallback } from 'react'

export function useScheduledTasks() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    setLoading(true)
    fetch('/api/scheduler/tasks')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { setTasks(d.tasks || []); setError(null) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const runNow = useCallback(async (taskId) => {
    const r = await fetch(`/api/scheduler/tasks/${taskId}/run`, { method: 'POST' })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      throw new Error(d.detail || `HTTP ${r.status}`)
    }
    return r.json()
  }, [])

  const deleteTask = useCallback(async (taskId) => {
    const r = await fetch(`/api/scheduler/tasks/${taskId}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    refresh()
  }, [refresh])

  const createTask = useCallback(async (body) => {
    const r = await fetch('/api/scheduler/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      throw new Error(d.detail?.errors?.join(', ') || d.detail || `HTTP ${r.status}`)
    }
    const result = await r.json()
    refresh()
    return result
  }, [refresh])

  const updateTask = useCallback(async (taskId, body) => {
    const r = await fetch(`/api/scheduler/tasks/${taskId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      throw new Error(d.detail?.errors?.join(', ') || d.detail || `HTTP ${r.status}`)
    }
    const result = await r.json()
    refresh()
    return result
  }, [refresh])

  const getRuns = useCallback(async (taskId, limit = 20) => {
    const r = await fetch(`/api/scheduler/tasks/${taskId}/runs?limit=${limit}`)
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const d = await r.json()
    return d.runs || []
  }, [])

  return { tasks, loading, error, refresh, runNow, deleteTask, createTask, updateTask, getRuns }
}
