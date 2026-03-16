import { useState, useEffect } from 'react'

export default function HotMemory({ projectId }) {
  const [memory, setMemory] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!projectId) return
    fetch(`/api/projects/${projectId}/status`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { setMemory(d.hot_memory || ''); setError(null) })
      .catch(err => setError(String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <div className="loading">Loading…</div>
  if (error) return <div className="error-msg">Error: {error}</div>
  if (!memory) return <div className="empty-state">No CLAUDE.md content found.</div>

  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 8 }}>
        CLAUDE.md — {memory.length} chars
      </div>
      <pre className="hot-memory">{memory}</pre>
    </div>
  )
}
