import { useState, useEffect } from 'react'
import { StatusBadge } from './StatusBadge.jsx'

function fmt(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return iso }
}

function duration(start, end) {
  if (!start || !end) return null
  const ms = new Date(end) - new Date(start)
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}

export default function TaskRunHistory({ task, getRuns, onClose }) {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(null)

  const load = () => {
    setLoading(true)
    getRuns(task.task_id)
      .then(setRuns)
      .catch(() => setRuns([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [task.task_id])

  // Close on Escape
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose])

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 640 }}>
        <div className="modal-header">
          <span className="modal-title">⏱ Run History — {task.name}</span>
          <button className="ghost icon" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body" style={{ padding: 0 }}>
          {loading ? (
            <div style={{ padding: 24, textAlign: 'center' }}>
              <span className="spinner" />
            </div>
          ) : runs.length === 0 ? (
            <div className="empty-state">
              <span className="empty-icon">📭</span>
              <span className="empty-text">No runs yet</span>
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Status', 'Started', 'Duration', ''].map(h => (
                    <th key={h} style={{
                      padding: '10px 16px', textAlign: 'left',
                      fontSize: 11, fontWeight: 600, color: 'var(--text-dim)',
                      textTransform: 'uppercase', letterSpacing: '0.4px',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {runs.map(run => (
                  <>
                    <tr
                      key={run.run_id}
                      style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
                      onClick={() => setExpanded(expanded === run.run_id ? null : run.run_id)}
                    >
                      <td style={{ padding: '10px 16px' }}><StatusBadge status={run.status} /></td>
                      <td style={{ padding: '10px 16px', fontSize: 13, color: 'var(--text-dim)' }}>{fmt(run.started_at)}</td>
                      <td style={{ padding: '10px 16px', fontSize: 13, color: 'var(--text-dim)' }}>
                        {duration(run.started_at, run.finished_at) || '—'}
                      </td>
                      <td style={{ padding: '10px 16px', color: 'var(--text-dim)', fontSize: 12 }}>
                        {(run.output || run.error) ? (expanded === run.run_id ? '▲' : '▼') : ''}
                      </td>
                    </tr>
                    {expanded === run.run_id && (run.output || run.error) && (
                      <tr key={`${run.run_id}-detail`} style={{ background: 'var(--bg)' }}>
                        <td colSpan={4} style={{ padding: '0 16px 12px' }}>
                          {run.error && (
                            <pre className="log" style={{ color: 'var(--error-fg)', marginTop: 10 }}>
                              {run.error}
                            </pre>
                          )}
                          {run.output && (
                            <pre className="log" style={{ marginTop: run.error ? 8 : 10 }}>
                              {run.output}
                            </pre>
                          )}
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="modal-footer">
          <button onClick={load} disabled={loading}>↺ Refresh</button>
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
