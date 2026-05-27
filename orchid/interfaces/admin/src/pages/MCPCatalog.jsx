import { useState, useEffect, useCallback } from 'react'

// ── Scope badge ───────────────────────────────────────────────────────────────

function scopeBadge(scope) {
  if (scope === 'admin-only') return <span className="badge badge-warning">admin-only</span>
  if (scope === 'private')    return <span className="badge badge-idle">private</span>
  return <span className="badge badge-info">shared</span>
}

// ── Add/Edit server modal ─────────────────────────────────────────────────────

function ServerModal({ server, onClose, onSave }) {
  const isEdit = !!server
  const [form, setForm] = useState({
    server_id:          server?.server_id || '',
    name:               server?.name || '',
    transport:          server?.transport || 'stdio',
    command:            server?.config?.command || '',
    url:                server?.config?.url || '',
    scope:              server?.scope || 'shared',
    allowed_roles:      (server?.allowed_roles || []).join(', '),
    allowed_users:      (server?.allowed_users || []).join(', '),
    requires_credential: server?.requires_credential || '',
  })
  const [error, setError]   = useState('')
  const [loading, setLoading] = useState(false)

  const f = (k) => (e) => setForm(prev => ({ ...prev, [k]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    const config = form.transport === 'stdio'
      ? { command: form.command }
      : { url: form.url }

    const payload = {
      server_id:          form.server_id,
      name:               form.name,
      transport:          form.transport,
      config,
      scope:              form.scope,
      allowed_roles:      form.allowed_roles.split(',').map(s => s.trim()).filter(Boolean),
      allowed_users:      form.allowed_users.split(',').map(s => s.trim()).filter(Boolean),
      requires_credential: form.requires_credential.trim() || null,
    }

    const url = isEdit
      ? `/api/admin/mcp/catalog/${server.server_id}`
      : '/api/admin/mcp/catalog'
    const method = isEdit ? 'PUT' : 'POST'

    try {
      const r = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(isEdit ? payload : payload),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setError(d.detail || 'Save failed'); return }
      onSave(d)
    } catch { setError('Network error') }
    finally { setLoading(false) }
  }

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <div className="modal-title">{isEdit ? `Edit ${server.name}` : 'Add MCP server'}</div>
          <button className="ghost" style={{ padding: '2px 8px' }} onClick={onClose}>✕</button>
        </div>
        <form onSubmit={submit}>
          <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="field">
                <label>Server ID</label>
                <input
                  value={form.server_id}
                  onChange={f('server_id')}
                  placeholder="e.g. gmail"
                  required
                  disabled={isEdit}
                />
              </div>
              <div className="field">
                <label>Name</label>
                <input value={form.name} onChange={f('name')} placeholder="Gmail" required />
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="field">
                <label>Transport</label>
                <select value={form.transport} onChange={f('transport')} disabled={isEdit}>
                  <option value="stdio">stdio</option>
                  <option value="http">http</option>
                </select>
              </div>
              <div className="field">
                <label>Scope</label>
                <select value={form.scope} onChange={f('scope')}>
                  <option value="shared">shared</option>
                  <option value="private">private</option>
                  <option value="admin-only">admin-only</option>
                </select>
              </div>
            </div>

            {form.transport === 'stdio' ? (
              <div className="field">
                <label>Command</label>
                <input
                  value={form.command}
                  onChange={f('command')}
                  placeholder="uvx orchid-mcp-gmail"
                  required={!isEdit}
                />
              </div>
            ) : (
              <div className="field">
                <label>URL</label>
                <input
                  type="url"
                  value={form.url}
                  onChange={f('url')}
                  placeholder="https://mcp.example.com/mcp"
                  required={!isEdit}
                />
              </div>
            )}

            <div className="field">
              <label>Allowed roles (comma-separated)</label>
              <input
                value={form.allowed_roles}
                onChange={f('allowed_roles')}
                placeholder="user, admin"
              />
              <span className="hint">Roles that can use this server via role-based access</span>
            </div>

            <div className="field">
              <label>Allowed user IDs (comma-separated)</label>
              <input
                value={form.allowed_users}
                onChange={f('allowed_users')}
                placeholder="alice, bob"
              />
              <span className="hint">Explicit user grants (override role check)</span>
            </div>

            <div className="field">
              <label>Requires credential (vault key name)</label>
              <input
                value={form.requires_credential}
                onChange={f('requires_credential')}
                placeholder="e.g. GMAIL_TOKEN"
              />
              <span className="hint">Users must have this key in their vault to use this server</span>
            </div>

            {error && <p style={{ color: 'var(--error-fg)', fontSize: 13 }}>{error}</p>}
          </div>
          <div className="modal-footer">
            <button type="button" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary" disabled={loading}>
              {loading ? 'Saving…' : isEdit ? 'Save changes' : 'Add server'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Grant/Revoke modal ────────────────────────────────────────────────────────

function AccessModal({ server, onClose, onDone }) {
  const [mode, setMode]     = useState('grant')
  const [type, setType]     = useState('role')
  const [value, setValue]   = useState('')
  const [error, setError]   = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    const body = type === 'role' ? { role: value } : { user_id: value }
    try {
      const r = await fetch(`/api/admin/mcp/catalog/${server.server_id}/${mode}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setError(d.detail || 'Failed'); return }
      onDone(d)
    } catch { setError('Network error') }
    finally { setLoading(false) }
  }

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <div className="modal-title">Manage access — {server.name}</div>
          <button className="ghost" style={{ padding: '2px 8px' }} onClick={onClose}>✕</button>
        </div>
        <form onSubmit={submit}>
          <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Current access */}
            <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
              <strong>Roles:</strong> {server.allowed_roles?.join(', ') || '—'}&nbsp;&nbsp;
              <strong>Users:</strong> {server.allowed_users?.join(', ') || '—'}
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
              {['grant', 'revoke'].map(m => (
                <button
                  key={m}
                  type="button"
                  className={mode === m ? 'primary' : ''}
                  style={{ flex: 1, textTransform: 'capitalize' }}
                  onClick={() => setMode(m)}
                >
                  {m}
                </button>
              ))}
            </div>

            <div className="field">
              <label>Type</label>
              <select value={type} onChange={e => setType(e.target.value)}>
                <option value="role">Role</option>
                <option value="user_id">User ID</option>
              </select>
            </div>

            <div className="field">
              <label>{type === 'role' ? 'Role name' : 'User ID'}</label>
              <input
                value={value}
                onChange={e => setValue(e.target.value)}
                placeholder={type === 'role' ? 'user' : 'alice'}
                required
                autoFocus
              />
            </div>

            {error && <p style={{ color: 'var(--error-fg)', fontSize: 13 }}>{error}</p>}
          </div>
          <div className="modal-footer">
            <button type="button" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary" disabled={loading || !value.trim()}>
              {loading ? 'Saving…' : `${mode.charAt(0).toUpperCase() + mode.slice(1)} access`}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── MCP Catalog page ──────────────────────────────────────────────────────────

export default function MCPCatalog() {
  const [servers, setServers]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState('')
  const [showAdd, setShowAdd]   = useState(false)
  const [editServer, setEditServer] = useState(null)
  const [accessServer, setAccessServer] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/admin/mcp/catalog')
      if (!r.ok) { setError('Failed to load catalog'); return }
      const d = await r.json()
      setServers(d.servers || [])
    } catch { setError('Network error') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const deleteServer = async (sid) => {
    if (!confirm(`Delete server "${sid}"? This cannot be undone.`)) return
    try {
      await fetch(`/api/admin/mcp/catalog/${sid}`, { method: 'DELETE' })
      load()
    } catch { /* ignore */ }
  }

  return (
    <div className="page">
      <div className="section-header">
        <h2 style={{ fontSize: 18, fontWeight: 700 }}>MCP Catalog</h2>
        <button className="primary" onClick={() => setShowAdd(true)}>+ Add server</button>
      </div>

      {error && <p style={{ color: 'var(--error-fg)', marginBottom: 14 }}>{error}</p>}

      {loading ? (
        <div style={{ display: 'flex', gap: 10, padding: 24, color: 'var(--text-dim)', alignItems: 'center' }}>
          <div className="spinner" /> Loading…
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Server</th>
                  <th>Transport</th>
                  <th>Scope</th>
                  <th>Roles</th>
                  <th>Users</th>
                  <th>Credential</th>
                  <th style={{ width: 140 }}></th>
                </tr>
              </thead>
              <tbody>
                {servers.length === 0 ? (
                  <tr>
                    <td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-mute)', padding: 32 }}>
                      No servers in catalog yet
                    </td>
                  </tr>
                ) : servers.map(s => (
                  <tr key={s.server_id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{s.name}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>
                        {s.server_id}
                      </div>
                    </td>
                    <td>
                      <code style={{ fontSize: 12 }}>{s.transport}</code>
                      <div style={{ fontSize: 11, color: 'var(--text-mute)' }}>
                        {s.config?.command || s.config?.url || ''}
                      </div>
                    </td>
                    <td>{scopeBadge(s.scope)}</td>
                    <td style={{ fontSize: 12 }}>{s.allowed_roles?.join(', ') || '—'}</td>
                    <td style={{ fontSize: 12 }}>{s.allowed_users?.join(', ') || '—'}</td>
                    <td style={{ fontSize: 12 }}>
                      {s.requires_credential
                        ? <code style={{ color: 'var(--warn-fg)' }}>{s.requires_credential}</code>
                        : <em style={{ color: 'var(--text-mute)' }}>none</em>}
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 5 }}>
                        <button style={{ fontSize: 11, padding: '3px 8px' }} onClick={() => setEditServer(s)}>
                          Edit
                        </button>
                        <button style={{ fontSize: 11, padding: '3px 8px' }} onClick={() => setAccessServer(s)}>
                          Access
                        </button>
                        <button className="danger" style={{ fontSize: 11, padding: '3px 8px' }} onClick={() => deleteServer(s.server_id)}>
                          ✕
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text-mute)' }}>
            {servers.length} server{servers.length !== 1 ? 's' : ''} in catalog
          </div>
        </div>
      )}

      {showAdd && (
        <ServerModal
          server={null}
          onClose={() => setShowAdd(false)}
          onSave={() => { setShowAdd(false); load() }}
        />
      )}
      {editServer && (
        <ServerModal
          server={editServer}
          onClose={() => setEditServer(null)}
          onSave={(updated) => {
            setServers(ss => ss.map(s => s.server_id === updated.server_id ? updated : s))
            setEditServer(null)
          }}
        />
      )}
      {accessServer && (
        <AccessModal
          server={accessServer}
          onClose={() => setAccessServer(null)}
          onDone={(updated) => {
            setServers(ss => ss.map(s => s.server_id === updated.server_id ? updated : s))
            setAccessServer(updated)  // keep modal open with updated data
          }}
        />
      )}
    </div>
  )
}
