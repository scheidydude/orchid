import { useState, useEffect, useCallback } from 'react'

// ── Helpers ───────────────────────────────────────────────────────────────────

function roleBadge(role) {
  if (role === 'admin')    return <span className="badge badge-warning">admin</span>
  if (role === 'readonly') return <span className="badge badge-idle">readonly</span>
  return <span className="badge badge-info">user</span>
}

function statusBadge(is_active) {
  return is_active
    ? <span className="badge badge-success">active</span>
    : <span className="badge badge-error">inactive</span>
}

// ── Invite modal ──────────────────────────────────────────────────────────────

function InviteModal({ onClose }) {
  const [email, setEmail]   = useState('')
  const [role, setRole]     = useState('user')
  const [result, setResult] = useState(null)
  const [error, setError]   = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const r = await fetch('/api/admin/invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, role }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setError(d.detail || 'Invite failed'); return }
      setResult(d)
    } catch { setError('Network error') }
    finally { setLoading(false) }
  }

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <div className="modal-title">Invite user</div>
          <button className="ghost" style={{ padding: '2px 8px' }} onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          {result ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <p style={{ color: 'var(--success-fg)', fontSize: 13 }}>
                ✓ Invite created{result.email_sent ? ' and sent by email' : ''}
              </p>
              <div className="field">
                <label>Invite URL (share with user)</label>
                <input readOnly value={result.invite_url} onFocus={e => e.target.select()} />
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                Token ID: <code>{result.token_id}</code> · expires in 48h
              </div>
            </div>
          ) : (
            <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div className="field">
                <label>Email address</label>
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required autoFocus
                />
              </div>
              <div className="field">
                <label>Role</label>
                <select value={role} onChange={e => setRole(e.target.value)}>
                  <option value="user">user</option>
                  <option value="admin">admin</option>
                  <option value="readonly">readonly</option>
                </select>
              </div>
              {error && <p style={{ color: 'var(--error-fg)', fontSize: 13 }}>{error}</p>}
              <div className="modal-footer" style={{ padding: 0 }}>
                <button type="button" onClick={onClose}>Cancel</button>
                <button type="submit" className="primary" disabled={loading}>
                  {loading ? 'Sending…' : 'Send invite'}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Edit user modal ───────────────────────────────────────────────────────────

function EditUserModal({ user, onClose, onSave }) {
  const [role, setRole]       = useState(user.role)
  const [email, setEmail]     = useState(user.email || '')
  const [projects, setProjects] = useState((user.projects || []).join('\n'))
  const [active, setActive]   = useState(user.is_active)
  const [error, setError]     = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    const projectList = projects.split('\n').map(p => p.trim()).filter(Boolean)
    try {
      const r = await fetch(`/api/auth/users/${user.user_id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role, email: email || null, projects: projectList, is_active: active,
        }),
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
          <div className="modal-title">Edit {user.username}</div>
          <button className="ghost" style={{ padding: '2px 8px' }} onClick={onClose}>✕</button>
        </div>
        <form onSubmit={submit}>
          <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="field">
              <label>Role</label>
              <select value={role} onChange={e => setRole(e.target.value)}>
                <option value="user">user</option>
                <option value="admin">admin</option>
                <option value="readonly">readonly</option>
              </select>
            </div>
            <div className="field">
              <label>Email</label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="user@example.com"
              />
            </div>
            <div className="field">
              <label>Allowed projects (one per line, empty = unrestricted)</label>
              <textarea
                rows={3}
                value={projects}
                onChange={e => setProjects(e.target.value)}
                placeholder="project-alpha&#10;project-beta"
                style={{ resize: 'vertical' }}
              />
              <span className="hint">Leave blank to allow all projects</span>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <input
                type="checkbox"
                checked={active}
                onChange={e => setActive(e.target.checked)}
                style={{ width: 'auto' }}
              />
              Account active
            </label>
            {error && <p style={{ color: 'var(--error-fg)', fontSize: 13 }}>{error}</p>}
          </div>
          <div className="modal-footer">
            <button type="button" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary" disabled={loading}>
              {loading ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Users page ────────────────────────────────────────────────────────────────

export default function Users() {
  const [users, setUsers]         = useState([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState('')
  const [showInvite, setShowInvite] = useState(false)
  const [editUser, setEditUser]   = useState(null)
  const [search, setSearch]       = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/auth/users')
      if (!r.ok) { setError('Failed to load users'); return }
      const d = await r.json()
      setUsers(d.users || [])
    } catch { setError('Network error') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const deactivate = async (uid) => {
    if (!confirm('Deactivate this user? Their sessions will be revoked.')) return
    try {
      const r = await fetch(`/api/auth/users/${uid}`, { method: 'DELETE' })
      if (r.ok) load()
    } catch { /* ignore */ }
  }

  const filtered = users.filter(u =>
    !search ||
    u.username.includes(search) ||
    (u.email || '').includes(search) ||
    u.user_id.includes(search)
  )

  return (
    <div className="page">
      <div className="section-header">
        <h2 style={{ fontSize: 18, fontWeight: 700 }}>Users</h2>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search…"
            style={{ width: 200 }}
          />
          <button className="primary" onClick={() => setShowInvite(true)}>+ Invite user</button>
        </div>
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
                  <th>User</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Projects</th>
                  <th style={{ width: 120 }}></th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-mute)', padding: 32 }}>
                      No users found
                    </td>
                  </tr>
                ) : filtered.map(u => (
                  <tr key={u.user_id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{u.username}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>
                        {u.user_id}
                      </div>
                    </td>
                    <td style={{ color: 'var(--text-dim)' }}>{u.email || '—'}</td>
                    <td>{roleBadge(u.role)}</td>
                    <td>{statusBadge(u.is_active)}</td>
                    <td style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                      {u.projects?.length ? u.projects.join(', ') : <em>all</em>}
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button
                          style={{ fontSize: 11, padding: '3px 10px' }}
                          onClick={() => setEditUser(u)}
                        >
                          Edit
                        </button>
                        {u.is_active && (
                          <button
                            className="danger"
                            style={{ fontSize: 11, padding: '3px 10px' }}
                            onClick={() => deactivate(u.user_id)}
                          >
                            Deactivate
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text-mute)' }}>
            {filtered.length} of {users.length} users
          </div>
        </div>
      )}

      {showInvite && (
        <InviteModal onClose={() => { setShowInvite(false); load() }} />
      )}
      {editUser && (
        <EditUserModal
          user={editUser}
          onClose={() => setEditUser(null)}
          onSave={(updated) => {
            setUsers(us => us.map(u => u.user_id === updated.user_id ? { ...u, ...updated } : u))
            setEditUser(null)
          }}
        />
      )}
    </div>
  )
}
