import { useState, useEffect } from 'react'
import SessionReplay from './SessionReplay.jsx'

export default function SessionHistory({ projectId }) {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    if (!projectId) return
    setSelected(null)
    fetch(`/api/projects/${projectId}/sessions`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(data => { setSessions(data); setError(null) })
      .catch(err => setError(String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <div className="loading">Loading sessions…</div>
  if (error) return <div className="error-msg">Error: {error}</div>

  if (selected) {
    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <button onClick={() => setSelected(null)} style={{ marginBottom: 10, alignSelf: 'flex-start' }}>
          ← Back to sessions
        </button>
        <SessionReplay projectId={projectId} sessionId={selected} />
      </div>
    )
  }

  if (!sessions.length) return <div className="empty-state">No session logs found.</div>

  return (
    <div>
      {sessions.map(s => (
        <div key={s.id} className="session-entry" onClick={() => setSelected(s.id)}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
            {s.id.replace('session_', '')}
          </div>
          <div className="session-meta">
            {(s.size / 1024).toFixed(1)} KB
          </div>
          <div className="session-meta">
            {s.modified?.slice(0, 16).replace('T', ' ')}
          </div>
        </div>
      ))}
    </div>
  )
}
