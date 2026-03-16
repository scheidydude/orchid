import { useState, useEffect } from 'react'

export default function SessionReplay({ projectId, sessionId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`/api/projects/${projectId}/sessions/${sessionId}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { setData(d); setError(null) })
      .catch(err => setError(String(err)))
      .finally(() => setLoading(false))
  }, [projectId, sessionId])

  if (loading) return <div className="loading">Loading session…</div>
  if (error) return <div className="error-msg">Error: {error}</div>
  if (!data) return null

  return (
    <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 8 }}>
        {sessionId} — {data.entries?.length || 0} entries
      </div>
      <div className="agent-stream" style={{ flex: 1 }}>
        {(data.entries || []).map((entry, i) => (
          <div key={i} className="stream-entry" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>
            <span className="stream-ts">{entry.timestamp?.slice(11, 19) || ''}</span>
            <span className="stream-type" style={{ color: 'var(--text-dim)' }}>{entry.type || 'raw'}</span>
            <span className="stream-msg" style={{ color: 'var(--text)' }}>
              {entry.raw || entry.content || entry.message || JSON.stringify(entry).slice(0, 200)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
