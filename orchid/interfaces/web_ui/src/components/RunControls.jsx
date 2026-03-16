import { useState } from 'react'

export default function RunControls({ projectId, runStatus, onRunChange }) {
  const [codeModel, setCodeModel] = useState('auto')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const startRun = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`/api/projects/${projectId}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'auto', code_model: codeModel === 'auto' ? null : codeModel }),
      })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail || `HTTP ${res.status}`)
      }
      onRunChange?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const stopRun = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`/api/projects/${projectId}/run`, { method: 'DELETE' })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail || `HTTP ${res.status}`)
      }
      onRunChange?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const { running, current_task, tasks_done } = runStatus

  return (
    <div className="run-controls">
      {running ? (
        <>
          <button className="danger" onClick={stopRun} disabled={loading}>
            ■ Stop
          </button>
          <div className="run-progress">
            <span className="project-running-dot" />
            {current_task ? `Running ${current_task}` : 'Starting…'}
            {tasks_done > 0 && <span style={{ marginLeft: 8, color: 'var(--text-dim)' }}>{tasks_done} done</span>}
          </div>
        </>
      ) : (
        <>
          <button className="primary" onClick={startRun} disabled={loading}>
            ▶ Start Run
          </button>
          <select
            value={codeModel}
            onChange={e => setCodeModel(e.target.value)}
            style={{ width: 100 }}
          >
            <option value="auto">auto</option>
            <option value="claude">claude</option>
            <option value="local">local</option>
          </select>
        </>
      )}
      {error && <span className="error-msg" style={{ padding: 0 }}>{error}</span>}
    </div>
  )
}
