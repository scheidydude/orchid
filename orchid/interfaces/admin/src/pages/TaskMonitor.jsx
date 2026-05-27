import { useState, useEffect, useCallback } from 'react'

const STATUS_BADGE = {
  success: 'badge-success',
  failure: 'badge-error',
  running: 'badge-info',
  timeout: 'badge-warning',
}

function statusBadge(s) {
  return <span className={`badge ${STATUS_BADGE[s] || 'badge-idle'}`}>{s}</span>
}

function fmtTime(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' }) }
  catch { return iso }
}

function fmtDuration(started, finished) {
  if (!started || !finished) return '—'
  const ms = new Date(finished) - new Date(started)
  if (isNaN(ms) || ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`
}

const PAGE_SIZE = 50

export default function TaskMonitor() {
  const [runs, setRuns]         = useState([])
  const [total, setTotal]       = useState(0)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState('')
  const [offset, setOffset]     = useState(0)
  const [filterUser, setFilterUser]   = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [expanded, setExpanded] = useState(null)

  const load = useCallback(async (off = 0) => {
    setLoading(true)
    setError('')
    const params = new URLSearchParams({
      limit: PAGE_SIZE,
      offset: off,
      ...(filterUser   ? { owner_id: filterUser } : {}),
      ...(filterStatus ? { status: filterStatus } : {}),
    })
    try {
      const r = await fetch(`/api/admin/runs?${params}`)
      if (!r.ok) { setError('Failed to load runs'); return }
      const d = await r.json()
      setRuns(d.runs || [])
      setTotal(d.total || 0)
      setOffset(off)
    } catch { setError('Network error') }
    finally { setLoading(false) }
  }, [filterUser, filterStatus])

  useEffect(() => { load(0) }, [load])

  const pages = Math.ceil(total / PAGE_SIZE)
  const currentPage = Math.floor(offset / PAGE_SIZE)

  return (
    <div className="page">
      <div className="section-header">
        <h2 style={{ fontSize: 18, fontWeight: 700 }}>Task Monitor</h2>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{total} runs</span>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
        <input
          value={filterUser}
          onChange={e => setFilterUser(e.target.value)}
          placeholder="Filter by user ID…"
          style={{ width: 220 }}
        />
        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} style={{ width: 140 }}>
          <option value="">All statuses</option>
          <option value="success">success</option>
          <option value="failure">failure</option>
          <option value="running">running</option>
          <option value="timeout">timeout</option>
        </select>
        <button onClick={() => load(0)}>Apply</button>
        <button className="ghost" onClick={() => { setFilterUser(''); setFilterStatus('') }}>Clear</button>
      </div>

      {error && <p style={{ color: 'var(--error-fg)', marginBottom: 14 }}>{error}</p>}

      {loading ? (
        <div style={{ display: 'flex', gap: 10, padding: 24, color: 'var(--text-dim)', alignItems: 'center' }}>
          <div className="spinner" /> Loading…
        </div>
      ) : (
        <>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 150 }}>Started</th>
                    <th>User</th>
                    <th>Task</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th style={{ width: 32 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {runs.length === 0 ? (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-mute)', padding: 32 }}>
                        No runs found
                      </td>
                    </tr>
                  ) : runs.map(r => (
                    <>
                      <tr key={r.run_id}
                          style={{ cursor: (r.output || r.error) ? 'pointer' : 'default' }}
                          onClick={() => (r.output || r.error) && setExpanded(expanded === r.run_id ? null : r.run_id)}>
                        <td style={{ fontSize: 12, fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                          {fmtTime(r.started_at)}
                        </td>
                        <td style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{r.owner_id}</td>
                        <td>
                          <div style={{ fontWeight: 500, fontSize: 13 }}>{r.task_name || r.task_id}</div>
                          <div style={{ fontSize: 11, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>{r.run_id}</div>
                        </td>
                        <td>
                          <code style={{ fontSize: 11 }}>{r.task_type}</code>
                        </td>
                        <td>{statusBadge(r.status)}</td>
                        <td style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                          {fmtDuration(r.started_at, r.finished_at)}
                        </td>
                        <td style={{ textAlign: 'center', fontSize: 11, color: 'var(--text-mute)' }}>
                          {(r.output || r.error) ? (expanded === r.run_id ? '▲' : '▼') : ''}
                        </td>
                      </tr>
                      {expanded === r.run_id && (r.output || r.error) && (
                        <tr key={`${r.run_id}-detail`}>
                          <td colSpan={7} style={{ background: 'var(--bg)', padding: '8px 16px' }}>
                            {r.error && (
                              <pre style={{ fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--error-fg)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', marginBottom: r.output ? 8 : 0 }}>
                                ✗ {r.error}
                              </pre>
                            )}
                            {r.output && (
                              <pre style={{ fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--text-dim)', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                {r.output.length > 2000 ? r.output.slice(0, 2000) + '\n…(truncated)' : r.output}
                              </pre>
                            )}
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {pages > 1 && (
            <div className="pagination" style={{ marginTop: 12 }}>
              <span>Page {currentPage + 1} of {pages}</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button disabled={offset === 0} onClick={() => load(Math.max(0, offset - PAGE_SIZE))}>← Prev</button>
                <button disabled={offset + PAGE_SIZE >= total} onClick={() => load(offset + PAGE_SIZE)}>Next →</button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
