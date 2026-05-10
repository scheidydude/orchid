import { useState, useEffect, useCallback } from 'react'
import TaskRow from './TaskRow.jsx'
import AddTaskModal from './AddTaskModal.jsx'

const STATUS_ORDER = ['IN_PROGRESS', 'BLOCKED', 'TODO', 'DONE', 'CANCELLED', 'SKIPPED']

const taskNum = (id) => parseInt((id || '').replace(/\D/g, ''), 10) || 0

export default function TaskBoard({ projectId, runStatus }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [filter, setFilter] = useState('active')
  const [sortMode, setSortMode] = useState('priority') // 'priority' | 'id'

  const fetchTasks = useCallback(async () => {
    if (!projectId) return
    try {
      const res = await fetch(`/api/projects/${projectId}/tasks`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setTasks(await res.json())
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => { fetchTasks() }, [fetchTasks])

  // Poll every 10s to pick up changes from CLI runs
  useEffect(() => {
    const id = setInterval(fetchTasks, 10000)
    return () => clearInterval(id)
  }, [fetchTasks])

  // Also refresh immediately when a run completes
  useEffect(() => {
    if (!runStatus.running) fetchTasks()
  }, [runStatus.running, fetchTasks])

  const handleStatusChange = async (taskId, newStatus) => {
    try {
      const res = await fetch(`/api/projects/${projectId}/tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const updated = await res.json()
      setTasks(prev => prev.map(t => t.id === taskId ? updated : t))
    } catch (err) {
      alert(`Failed: ${err.message}`)
    }
  }

  const handleRunTask = async (taskId) => {
    try {
      const res = await fetch(`/api/projects/${projectId}/tasks/${taskId}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch (err) {
      alert(`Failed to start task: ${err.message}`)
    }
  }

  const filtered = tasks.filter(t => {
    if (filter === 'active') return ['TODO', 'IN_PROGRESS', 'BLOCKED'].includes(t.status)
    if (filter === 'done') return t.status === 'DONE'
    if (filter === 'skipped') return t.status === 'SKIPPED'
    return true
  }).sort((a, b) => {
    const oa = STATUS_ORDER.indexOf(a.status)
    const ob = STATUS_ORDER.indexOf(b.status)
    if (oa !== ob) return oa - ob
    if (sortMode === 'priority' && a.priority !== b.priority) return a.priority - b.priority
    return taskNum(a.id) - taskNum(b.id)
  })

  if (loading) return <div className="loading">Loading tasks…</div>
  if (error) return <div className="error-msg">Error: {error}</div>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="task-board-toolbar">
        <div className="task-filter-bar">
          {['active', 'done', 'skipped', 'all'].map(f => {
            const count = tasks.filter(t => {
              if (f === 'active') return ['TODO', 'IN_PROGRESS', 'BLOCKED'].includes(t.status)
              if (f === 'done') return t.status === 'DONE'
              if (f === 'skipped') return t.status === 'SKIPPED'
              return true
            }).length
            return (
              <button
                key={f}
                onClick={() => setFilter(f)}
                style={{
                  padding: '4px 10px',
                  fontSize: 12,
                  background: filter === f ? 'var(--accent)' : undefined,
                  borderColor: filter === f ? 'var(--accent)' : undefined,
                  color: filter === f ? '#fff' : undefined,
                }}
              >
                {f} ({count})
              </button>
            )
          })}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginLeft: 8 }}>
          <span style={{ fontSize: 11, color: 'var(--text-dim)', marginRight: 2 }}>Sort:</span>
          {[
            { key: 'priority', label: 'Priority + ID' },
            { key: 'id',       label: 'ID' },
          ].map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setSortMode(key)}
              title={key === 'priority' ? 'Sort by status → priority → task ID' : 'Sort by status → task ID'}
              style={{
                padding: '3px 8px',
                fontSize: 11,
                background: sortMode === key ? 'var(--accent)' : undefined,
                borderColor: sortMode === key ? 'var(--accent)' : undefined,
                color: sortMode === key ? '#fff' : undefined,
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <span style={{ fontSize: 12, color: 'var(--text-dim)', marginLeft: 8 }}>{filtered.length} tasks</span>
        <button
          className="primary"
          onClick={() => setShowAdd(true)}
          style={{ marginLeft: 'auto', padding: '4px 12px', fontSize: 12 }}
        >
          + Add Task
        </button>
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state">
          {filter === 'active' ? 'No active tasks. Add a task to get started.' : `No ${filter} tasks.`}
        </div>
      ) : (
        <div className="task-board" style={{ flex: 1, overflowY: 'auto' }}>
          {filtered.map(t => (
            <TaskRow
              key={t.id}
              task={t}
              onStatusChange={handleStatusChange}
              onRunTask={handleRunTask}
              running={runStatus.running}
            />
          ))}
        </div>
      )}

      {showAdd && (
        <AddTaskModal
          projectId={projectId}
          onClose={() => setShowAdd(false)}
          onCreated={(task) => { setTasks(prev => [...prev, task]); setFilter('active') }}
        />
      )}
    </div>
  )
}
