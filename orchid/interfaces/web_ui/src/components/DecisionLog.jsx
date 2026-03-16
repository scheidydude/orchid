import { useState, useEffect } from 'react'

export default function DecisionLog({ projectId }) {
  const [decisions, setDecisions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!projectId) return
    fetch(`/api/projects/${projectId}/decisions`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(data => { setDecisions(data); setError(null) })
      .catch(err => setError(String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <div className="loading">Loading decisions…</div>
  if (error) return <div className="error-msg">Error: {error}</div>
  if (!decisions.length) return <div className="empty-state">No decisions recorded.</div>

  return (
    <div>
      {[...decisions].reverse().map(d => (
        <div key={d.id} className="decision-entry">
          <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', marginBottom: 4 }}>
            <span className="decision-id">{d.id}</span>
            <span className="decision-title">{d.title}</span>
            <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-dim)' }}>
              {d.timestamp?.slice(0, 10)}
            </span>
          </div>
          <div className="decision-body">
            <strong>Decision:</strong> {d.decision}
          </div>
          {d.rationale && (
            <div className="decision-body" style={{ marginTop: 4 }}>
              <strong>Rationale:</strong> {d.rationale}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
