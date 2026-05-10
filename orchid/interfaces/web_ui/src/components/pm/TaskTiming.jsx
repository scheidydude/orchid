import { useState, useEffect } from 'react'

function rowColor(rec, itersMax) {
  if (rec.status === 'blocked') return 'rgba(248,81,73,0.12)'
  if (rec.iters_used >= itersMax * 0.8) return 'rgba(240,136,62,0.12)'
  if (rec.iters_used <= 5 && rec.status === 'done') return 'rgba(86,211,100,0.08)'
  return undefined
}

function formatDuration(s) {
  if (s == null) return '—'
  if (s < 60) return `${s.toFixed(1)}s`
  return `${(s / 60).toFixed(1)}m`
}

function countActions(actionCounts) {
  if (!actionCounts) return 0
  return Object.values(actionCounts).reduce((a, b) => a + b, 0)
}

export default function TaskTiming({ projectId }) {
  const [metrics, setMetrics] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!projectId) return
    setLoading(true)
    fetch(`/api/projects/${projectId}/metrics`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(data => { setMetrics(data); setError(null) })
      .catch(err => setError(String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <div className="loading" style={{ padding: 12 }}>Loading metrics…</div>
  if (error) return <div className="error-msg">Error: {error}</div>
  if (metrics.length === 0) {
    return <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: 12, fontStyle: 'italic' }}>
      No task metrics yet. Run some tasks to collect timing data.
    </div>
  }

  const sorted = [...metrics].sort((a, b) => (b.duration_s ?? 0) - (a.duration_s ?? 0))
  const itersMax = sorted[0]?.iters_max ?? 15

  const durations = metrics.map(m => m.duration_s).filter(d => d != null)
  const avgDuration = durations.length > 0 ? durations.reduce((a, b) => a + b, 0) / durations.length : 0
  const fastest = durations.length > 0 ? Math.min(...durations) : null
  const slowest = durations.length > 0 ? Math.max(...durations) : null
  const totalBlocked = metrics.filter(m => m.status === 'blocked').length

  return (
    <div>
      {/* Summary stats */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 14, flexWrap: 'wrap' }}>
        {[
          { label: 'Avg duration', value: formatDuration(avgDuration) },
          { label: 'Fastest', value: fastest != null ? formatDuration(fastest) : '—' },
          { label: 'Slowest', value: slowest != null ? formatDuration(slowest) : '—' },
          { label: 'Total blocked', value: totalBlocked, color: totalBlocked > 0 ? '#f85149' : undefined },
          { label: 'Total tasks', value: metrics.length },
        ].map(s => (
          <div key={s.label} style={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 12px', minWidth: 90 }}>
            <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 2 }}>{s.label}</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: s.color || undefined }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-dim)' }}>
              <th style={{ padding: '6px 8px', textAlign: 'left', whiteSpace: 'nowrap' }}>Task</th>
              <th style={{ padding: '6px 8px', textAlign: 'left' }}>Title</th>
              <th style={{ padding: '6px 8px', textAlign: 'center' }}>Status</th>
              <th style={{ padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>Duration</th>
              <th style={{ padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>Iters</th>
              <th style={{ padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>Actions</th>
              <th style={{ padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>CPU</th>
              <th style={{ padding: '6px 8px', textAlign: 'left' }}>Model</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((rec, i) => (
              <tr
                key={`${rec.task_id}-${i}`}
                style={{ borderBottom: '1px solid var(--border)', background: rowColor(rec, itersMax) }}
              >
                <td style={{ padding: '5px 8px', fontFamily: 'var(--mono)', color: 'var(--accent)' }}>{rec.task_id}</td>
                <td style={{ padding: '5px 8px', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={rec.title}>{rec.title}</td>
                <td style={{ padding: '5px 8px', textAlign: 'center' }}>
                  <span style={{
                    fontSize: 10, padding: '2px 7px', borderRadius: 10,
                    background: rec.status === 'done' ? 'rgba(86,211,100,0.2)' : 'rgba(248,81,73,0.2)',
                    color: rec.status === 'done' ? '#56d364' : '#f85149',
                  }}>
                    {rec.status}
                  </span>
                </td>
                <td style={{ padding: '5px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>{formatDuration(rec.duration_s)}</td>
                <td style={{ padding: '5px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>
                  <span style={{ color: rec.iters_used >= itersMax * 0.8 ? '#f0883e' : undefined }}>
                    {rec.iters_used}/{rec.iters_max}
                  </span>
                </td>
                <td style={{ padding: '5px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>{countActions(rec.action_counts)}</td>
                <td style={{ padding: '5px 8px', textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                  {rec.cpu_seconds != null && rec.cpu_seconds > 0 ? `${rec.cpu_seconds.toFixed(1)}s` : '—'}
                </td>
                <td style={{ padding: '5px 8px', color: 'var(--text-dim)' }}>{rec.model}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
