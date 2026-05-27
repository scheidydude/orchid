import { useState, useEffect, useCallback } from 'react'

const ACTION_COLORS = {
  login:          'badge-success',
  login_failed:   'badge-error',
  logout:         'badge-idle',
  register:       'badge-info',
  api_key_created:'badge-info',
  api_key_revoked:'badge-warning',
  user_updated:   'badge-warning',
  user_deactivated:'badge-error',
  invite_sent:    'badge-info',
  invite_accepted:'badge-success',
  credential_updated: 'badge-info',
  credential_deleted: 'badge-warning',
  mcp_server_created: 'badge-success',
  mcp_server_deleted: 'badge-error',
  mcp_access_granted: 'badge-success',
  mcp_access_revoked: 'badge-warning',
}

function actionBadge(action) {
  const cls = ACTION_COLORS[action] || 'badge-idle'
  return <span className={`badge ${cls}`}>{action}</span>
}

function resultBadge(result) {
  if (result === 'success') return <span className="badge badge-success">success</span>
  if (result === 'failure') return <span className="badge badge-error">failure</span>
  if (result === 'denied')  return <span className="badge badge-warning">denied</span>
  return <span className="badge badge-idle">{result}</span>
}

function fmtTime(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' })
  } catch { return iso }
}

const PAGE_SIZE = 50

export default function AuditLog() {
  const [events, setEvents] = useState([])
  const [total, setTotal]   = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState('')
  const [offset, setOffset] = useState(0)
  const [filterUser, setFilterUser]   = useState('')
  const [filterAction, setFilterAction] = useState('')
  const [expanded, setExpanded] = useState(null)

  const load = useCallback(async (off = 0) => {
    setLoading(true)
    setError('')
    const params = new URLSearchParams({
      limit: PAGE_SIZE,
      offset: off,
      ...(filterUser   ? { user_id: filterUser }   : {}),
      ...(filterAction ? { action: filterAction } : {}),
    })
    try {
      const r = await fetch(`/api/audit?${params}`)
      if (!r.ok) { setError('Failed to load audit log'); return }
      const d = await r.json()
      setEvents(d.events || [])
      setTotal(d.total || 0)
      setOffset(off)
    } catch { setError('Network error') }
    finally { setLoading(false) }
  }, [filterUser, filterAction])

  useEffect(() => { load(0) }, [load])

  const pages = Math.ceil(total / PAGE_SIZE)
  const currentPage = Math.floor(offset / PAGE_SIZE)

  return (
    <div className="page">
      <div className="section-header">
        <h2 style={{ fontSize: 18, fontWeight: 700 }}>Audit Log</h2>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{total} events</span>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
        <input
          value={filterUser}
          onChange={e => setFilterUser(e.target.value)}
          placeholder="Filter by user ID…"
          style={{ width: 220 }}
        />
        <input
          value={filterAction}
          onChange={e => setFilterAction(e.target.value)}
          placeholder="Filter by action…"
          style={{ width: 220 }}
        />
        <button onClick={() => load(0)}>Apply</button>
        <button className="ghost" onClick={() => { setFilterUser(''); setFilterAction('') }}>Clear</button>
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
                    <th style={{ width: 150 }}>Time</th>
                    <th>User</th>
                    <th>Action</th>
                    <th>Resource</th>
                    <th>Result</th>
                    <th>IP</th>
                    <th style={{ width: 40 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {events.length === 0 ? (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-mute)', padding: 32 }}>
                        No events found
                      </td>
                    </tr>
                  ) : events.map(ev => (
                    <>
                      <tr key={ev.event_id} style={{ cursor: ev.detail ? 'pointer' : 'default' }}
                          onClick={() => ev.detail && setExpanded(expanded === ev.event_id ? null : ev.event_id)}>
                        <td style={{ fontSize: 12, fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                          {fmtTime(ev.timestamp)}
                        </td>
                        <td style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{ev.user_id}</td>
                        <td>{actionBadge(ev.action)}</td>
                        <td style={{ fontSize: 12, fontFamily: 'var(--mono)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {ev.resource}
                        </td>
                        <td>{resultBadge(ev.result)}</td>
                        <td style={{ fontSize: 11, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>{ev.ip || '—'}</td>
                        <td style={{ textAlign: 'center', color: 'var(--text-mute)', fontSize: 11 }}>
                          {ev.detail ? (expanded === ev.event_id ? '▲' : '▼') : ''}
                        </td>
                      </tr>
                      {expanded === ev.event_id && ev.detail && (
                        <tr key={`${ev.event_id}-detail`}>
                          <td colSpan={7} style={{ background: 'var(--bg)', padding: '8px 16px' }}>
                            <pre style={{ fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--text-dim)', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                              {(() => { try { return JSON.stringify(JSON.parse(ev.detail), null, 2) } catch { return ev.detail } })()}
                            </pre>
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Pagination */}
          {pages > 1 && (
            <div className="pagination" style={{ marginTop: 12 }}>
              <span>Page {currentPage + 1} of {pages}</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  disabled={offset === 0}
                  onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
                >
                  ← Prev
                </button>
                <button
                  disabled={offset + PAGE_SIZE >= total}
                  onClick={() => load(offset + PAGE_SIZE)}
                >
                  Next →
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
