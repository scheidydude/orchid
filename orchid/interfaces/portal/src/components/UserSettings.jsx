import { useState } from 'react'

export default function UserSettings({ user }) {
  const [currentPw, setCurrentPw]   = useState('')
  const [newPw, setNewPw]           = useState('')
  const [confirmPw, setConfirmPw]   = useState('')
  const [pwError, setPwError]       = useState('')
  const [pwSuccess, setPwSuccess]   = useState(false)
  const [saving, setSaving]         = useState(false)

  const handleChangePassword = async (e) => {
    e.preventDefault()
    setPwError('')
    setPwSuccess(false)
    if (newPw !== confirmPw) { setPwError('Passwords do not match'); return }
    if (newPw.length < 8)    { setPwError('Password must be at least 8 characters'); return }
    setSaving(true)
    try {
      const r = await fetch('/api/auth/me/password', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setPwError(d.detail || 'Password change failed')
        return
      }
      setPwSuccess(true)
      setCurrentPw(''); setNewPw(''); setConfirmPw('')
    } catch {
      setPwError('Network error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page" style={{ maxWidth: 560 }}>
      <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 24 }}>Settings</h2>

      {/* Profile */}
      <section style={{ marginBottom: 32 }}>
        <div className="section-title" style={{ marginBottom: 14 }}>Profile</div>
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: 10, alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Username</span>
            <span style={{ fontWeight: 600 }}>{user.username}</span>

            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Email</span>
            <span>{user.email || <em style={{ color: 'var(--text-mute)' }}>not set</em>}</span>

            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Role</span>
            <span style={{ textTransform: 'capitalize' }}>{user.role}</span>

            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>User ID</span>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--text-dim)' }}>{user.user_id}</span>
          </div>
        </div>
      </section>

      {/* Change password */}
      <section style={{ marginBottom: 32 }}>
        <div className="section-title" style={{ marginBottom: 14 }}>Change Password</div>
        <div className="card">
          <form onSubmit={handleChangePassword} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="field">
              <label>Current password</label>
              <input
                type="password"
                value={currentPw}
                onChange={e => setCurrentPw(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
            <div className="field">
              <label>New password</label>
              <input
                type="password"
                value={newPw}
                onChange={e => setNewPw(e.target.value)}
                autoComplete="new-password"
                required
                minLength={8}
              />
              <span className="hint">Minimum 8 characters</span>
            </div>
            <div className="field">
              <label>Confirm new password</label>
              <input
                type="password"
                value={confirmPw}
                onChange={e => setConfirmPw(e.target.value)}
                autoComplete="new-password"
                required
              />
            </div>
            {pwError   && <p style={{ color: 'var(--error-fg)',   fontSize: 13 }}>{pwError}</p>}
            {pwSuccess  && <p style={{ color: 'var(--success-fg)', fontSize: 13 }}>✓ Password updated</p>}
            <div>
              <button type="submit" className="primary" disabled={saving}>
                {saving ? 'Saving…' : 'Update password'}
              </button>
            </div>
          </form>
        </div>
      </section>

      {/* API Keys — read-only list placeholder */}
      <section style={{ marginBottom: 32 }}>
        <div className="section-title" style={{ marginBottom: 14 }}>API Keys</div>
        <ApiKeyManager />
      </section>

      {/* Phase 2 placeholders */}
      <section style={{ marginBottom: 32 }}>
        <div className="section-title" style={{ marginBottom: 14 }}>Credentials <span style={{ fontSize: 11, color: 'var(--text-mute)', fontWeight: 400, marginLeft: 6 }}>coming in Phase 2</span></div>
        <div className="card" style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          Per-user LLM provider keys and third-party credentials will be stored here (encrypted).
        </div>
      </section>

      <section>
        <div className="section-title" style={{ marginBottom: 14 }}>Notifications <span style={{ fontSize: 11, color: 'var(--text-mute)', fontWeight: 400, marginLeft: 6 }}>coming in Phase 2</span></div>
        <div className="card" style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          Configure email, Telegram, and Slack notifications for scheduled task results.
        </div>
      </section>
    </div>
  )
}

// ── API Key Manager ───────────────────────────────────────────────────────────

function ApiKeyManager() {
  const [keys, setKeys]         = useState(null)
  const [loading, setLoading]   = useState(false)
  const [loaded, setLoaded]     = useState(false)
  const [creating, setCreating] = useState(false)
  const [newKeyName, setNewKeyName] = useState('')
  const [newKeySecret, setNewKeySecret] = useState(null)
  const [error, setError]       = useState(null)

  const loadKeys = async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/auth/apikeys')
      const d = await r.json()
      setKeys(d.keys || [])
      setLoaded(true)
    } catch { setError('Failed to load keys') }
    finally { setLoading(false) }
  }

  const createKey = async (e) => {
    e.preventDefault()
    setError(null)
    setCreating(true)
    try {
      const r = await fetch('/api/auth/apikeys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newKeyName }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setNewKeySecret(d.key)
      setNewKeyName('')
      loadKeys()
    } catch (e) { setError(e.message) }
    finally { setCreating(false) }
  }

  const revokeKey = async (keyId) => {
    try {
      await fetch(`/api/auth/apikeys/${keyId}`, { method: 'DELETE' })
      loadKeys()
    } catch { setError('Revoke failed') }
  }

  if (!loaded) {
    return (
      <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 13, color: 'var(--text-dim)' }}>Manage API keys for programmatic access</span>
        <button onClick={loadKeys} disabled={loading}>
          {loading ? 'Loading…' : 'View keys'}
        </button>
      </div>
    )
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {newKeySecret && (
        <div style={{
          background: 'var(--success)22', border: '1px solid var(--success)',
          borderRadius: 6, padding: '12px 14px',
        }}>
          <div style={{ fontSize: 12, color: 'var(--success-fg)', fontWeight: 600, marginBottom: 6 }}>
            ✓ Key created — copy it now, it won't be shown again:
          </div>
          <code style={{
            fontFamily: 'var(--mono)', fontSize: 12,
            background: 'var(--bg)', padding: '6px 10px',
            borderRadius: 4, display: 'block',
            wordBreak: 'break-all', color: 'var(--text)',
          }}>
            {newKeySecret}
          </code>
          <button
            style={{ marginTop: 8, fontSize: 12 }}
            onClick={() => { navigator.clipboard?.writeText(newKeySecret); setNewKeySecret(null) }}
          >
            Copy & dismiss
          </button>
        </div>
      )}

      {keys?.length === 0 ? (
        <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>No API keys yet.</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {keys?.map(k => (
            <div key={k.key_id} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '8px 0', borderBottom: '1px solid var(--border)',
            }}>
              <div>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{k.name}</span>
                <span style={{ fontSize: 11, color: 'var(--text-mute)', fontFamily: 'var(--mono)', marginLeft: 10 }}>
                  {k.key_id}
                </span>
              </div>
              <button
                className="danger"
                style={{ fontSize: 11, padding: '3px 10px' }}
                onClick={() => revokeKey(k.key_id)}
              >
                Revoke
              </button>
            </div>
          ))}
        </div>
      )}

      <form onSubmit={createKey} style={{ display: 'flex', gap: 8, marginTop: 4 }}>
        <input
          value={newKeyName}
          onChange={e => setNewKeyName(e.target.value)}
          placeholder="Key name (e.g. CI pipeline)"
          required
          style={{ flex: 1 }}
        />
        <button type="submit" className="primary" disabled={creating || !newKeyName.trim()} style={{ whiteSpace: 'nowrap' }}>
          {creating ? 'Creating…' : '+ New key'}
        </button>
      </form>

      {error && <p style={{ color: 'var(--error-fg)', fontSize: 12 }}>{error}</p>}
    </div>
  )
}
