import { useState, useEffect } from 'react'

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

      {/* API Keys */}
      <section style={{ marginBottom: 32 }}>
        <div className="section-title" style={{ marginBottom: 14 }}>API Keys</div>
        <ApiKeyManager />
      </section>

      {/* Credentials vault */}
      <section style={{ marginBottom: 32 }}>
        <div className="section-title" style={{ marginBottom: 14 }}>Credentials</div>
        <CredentialVault />
      </section>

      {/* MCP Servers */}
      <section style={{ marginBottom: 32 }}>
        <div className="section-title" style={{ marginBottom: 14 }}>MCP Servers</div>
        <MCPServers />
      </section>

      {/* Notifications */}
      <section>
        <div className="section-title" style={{ marginBottom: 14 }}>Notifications</div>
        <NotificationConfig user={user} />
      </section>
    </div>
  )
}

// ── Credential Vault ──────────────────────────────────────────────────────────

function CredentialVault() {
  const [keys, setKeys]         = useState(null)
  const [loaded, setLoaded]     = useState(false)
  const [loading, setLoading]   = useState(false)
  const [adding, setAdding]     = useState(false)
  const [newKey, setNewKey]     = useState('')
  const [newVal, setNewVal]     = useState('')
  const [saving, setSaving]     = useState(false)
  const [error, setError]       = useState(null)
  const [noVaultKey, setNoVaultKey] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/user/credentials')
      if (r.status === 503) { setNoVaultKey(true); return }
      const d = await r.json()
      setKeys(d.keys || [])
      setLoaded(true)
    } catch { setError('Failed to load credentials') }
    finally { setLoading(false) }
  }

  const addCredential = async (e) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      const r = await fetch(`/api/user/credentials/${encodeURIComponent(newKey)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: newVal }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || 'Save failed')
        return
      }
      setNewKey(''); setNewVal(''); setAdding(false)
      load()
    } catch { setError('Network error') }
    finally { setSaving(false) }
  }

  const deleteCredential = async (key) => {
    setError(null)
    try {
      await fetch(`/api/user/credentials/${encodeURIComponent(key)}`, { method: 'DELETE' })
      load()
    } catch { setError('Delete failed') }
  }

  if (noVaultKey) {
    return (
      <div className="card" style={{ color: 'var(--text-dim)', fontSize: 13 }}>
        ⚠️ Credential vault is not configured on this server.
        Ask your admin to set <code>ORCHID_VAULT_KEY</code>.
      </div>
    )
  }

  if (!loaded) {
    return (
      <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 13, color: 'var(--text-dim)' }}>
          Encrypted storage for API keys and tokens used by your scheduled tasks.
        </span>
        <button onClick={load} disabled={loading}>{loading ? 'Loading…' : 'View credentials'}</button>
      </div>
    )
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <p style={{ fontSize: 12, color: 'var(--text-dim)', margin: 0 }}>
        Values are encrypted at rest. Names are visible; secrets are write-only.
      </p>

      {keys?.length === 0 ? (
        <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>No credentials stored yet.</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {keys?.map(k => (
            <div key={k} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '8px 0', borderBottom: '1px solid var(--border)',
            }}>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 13 }}>{k}</span>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: 'var(--text-mute)' }}>••••••••</span>
                <button
                  className="danger"
                  style={{ fontSize: 11, padding: '3px 10px' }}
                  onClick={() => deleteCredential(k)}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {adding ? (
        <form onSubmit={addCredential} style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
          <div className="field">
            <label>Credential name</label>
            <input
              value={newKey}
              onChange={e => setNewKey(e.target.value)}
              placeholder="e.g. ANTHROPIC_API_KEY"
              pattern="[\w\-\.]+"
              title="Letters, digits, underscores, hyphens, and dots only"
              required
              autoFocus
            />
            <span className="hint">Used to reference this credential in task configs</span>
          </div>
          <div className="field">
            <label>Secret value</label>
            <input
              type="password"
              value={newVal}
              onChange={e => setNewVal(e.target.value)}
              placeholder="Paste secret here"
              required
              autoComplete="off"
            />
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="submit" className="primary" disabled={saving || !newKey.trim() || !newVal.trim()}>
              {saving ? 'Saving…' : 'Save credential'}
            </button>
            <button type="button" onClick={() => { setAdding(false); setNewKey(''); setNewVal('') }}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <button onClick={() => setAdding(true)} style={{ alignSelf: 'flex-start' }}>
          + Add credential
        </button>
      )}

      {error && <p style={{ color: 'var(--error-fg)', fontSize: 12 }}>{error}</p>}
    </div>
  )
}

// ── Notification Config ───────────────────────────────────────────────────────

function NotificationConfig({ user }) {
  const [cfg, setCfg]         = useState(null)
  const [loaded, setLoaded]   = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving]   = useState(false)
  const [saved, setSaved]     = useState(false)
  const [error, setError]     = useState(null)

  const load = async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/user/config/notifications')
      const d = await r.json()
      setCfg({
        email_enabled:     d.email_enabled     ?? false,
        email_address:     d.email_address     ?? user.email ?? '',
        telegram_enabled:  d.telegram_enabled  ?? false,
        telegram_chat_id:  d.telegram_chat_id  ?? '',
        slack_enabled:     d.slack_enabled     ?? false,
        slack_user_id:     d.slack_user_id     ?? '',
        notify_on_success: d.notify_on_success ?? false,
        notify_on_failure: d.notify_on_failure ?? true,
      })
      setLoaded(true)
    } catch { setError('Failed to load notification settings') }
    finally { setLoading(false) }
  }

  const save = async (e) => {
    e.preventDefault()
    setSaving(true); setSaved(false); setError(null)
    try {
      const r = await fetch('/api/user/config/notifications', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || 'Save failed')
        return
      }
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch { setError('Network error') }
    finally { setSaving(false) }
  }

  const set = (key, val) => setCfg(c => ({ ...c, [key]: val }))

  if (!loaded) {
    return (
      <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 13, color: 'var(--text-dim)' }}>
          Configure email, Telegram, and Slack alerts for scheduled task results.
        </span>
        <button onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Configure'}</button>
      </div>
    )
  }

  return (
    <form className="card" onSubmit={save} style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* When to notify */}
      <div>
        <div style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 600, marginBottom: 10 }}>
          NOTIFY WHEN
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', fontSize: 13 }}>
            <input
              type="checkbox"
              checked={cfg.notify_on_failure}
              onChange={e => set('notify_on_failure', e.target.checked)}
            />
            Task fails or times out
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', fontSize: 13 }}>
            <input
              type="checkbox"
              checked={cfg.notify_on_success}
              onChange={e => set('notify_on_success', e.target.checked)}
            />
            Task succeeds
          </label>
        </div>
      </div>

      {/* Email */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', marginBottom: 10 }}>
          <input
            type="checkbox"
            checked={cfg.email_enabled}
            onChange={e => set('email_enabled', e.target.checked)}
          />
          <span style={{ fontWeight: 600, fontSize: 13 }}>Email</span>
        </label>
        {cfg.email_enabled && (
          <div className="field">
            <label>Email address</label>
            <input
              type="email"
              value={cfg.email_address}
              onChange={e => set('email_address', e.target.value)}
              placeholder={user.email || 'you@example.com'}
            />
            <span className="hint">Defaults to your account email if left blank</span>
          </div>
        )}
      </div>

      {/* Telegram */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', marginBottom: 10 }}>
          <input
            type="checkbox"
            checked={cfg.telegram_enabled}
            onChange={e => set('telegram_enabled', e.target.checked)}
          />
          <span style={{ fontWeight: 600, fontSize: 13 }}>Telegram</span>
        </label>
        {cfg.telegram_enabled && (
          <div className="field">
            <label>Chat ID</label>
            <input
              value={cfg.telegram_chat_id}
              onChange={e => set('telegram_chat_id', e.target.value)}
              placeholder="e.g. 123456789"
            />
            <span className="hint">
              Get your chat ID by messaging{' '}
              <a href="https://t.me/userinfobot" target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
                @userinfobot
              </a>{' '}
              on Telegram
            </span>
          </div>
        )}
      </div>

      {/* Slack */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', marginBottom: 10 }}>
          <input
            type="checkbox"
            checked={cfg.slack_enabled}
            onChange={e => set('slack_enabled', e.target.checked)}
          />
          <span style={{ fontWeight: 600, fontSize: 13 }}>Slack</span>
        </label>
        {cfg.slack_enabled && (
          <div className="field">
            <label>Slack user ID</label>
            <input
              value={cfg.slack_user_id}
              onChange={e => set('slack_user_id', e.target.value)}
              placeholder="e.g. U12345678"
            />
            <span className="hint">
              Find yours in Slack: click your name → View profile → ⋯ → Copy member ID
            </span>
          </div>
        )}
      </div>

      {/* Save */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button type="submit" className="primary" disabled={saving}>
          {saving ? 'Saving…' : 'Save notifications'}
        </button>
        {saved  && <span style={{ fontSize: 13, color: 'var(--success-fg)' }}>✓ Saved</span>}
        {error  && <span style={{ fontSize: 13, color: 'var(--error-fg)' }}>{error}</span>}
      </div>
    </form>
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

// ── MCP Servers ───────────────────────────────────────────────────────────────

function MCPServers() {
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(false)
  const [loaded, setLoaded]     = useState(false)
  const [adding, setAdding]     = useState(false)
  const [error, setError]       = useState(null)
  const [form, setForm]         = useState({ name: '', transport: 'stdio', command: '', url: '' })
  const [saving, setSaving]     = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch('/api/user/mcp/servers')
      if (!r.ok) { setError('Failed to load MCP servers'); return }
      setData(await r.json())
      setLoaded(true)
    } catch { setError('Network error') }
    finally { setLoading(false) }
  }

  const addServer = async (e) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      const body = {
        name: form.name,
        transport: form.transport,
        ...(form.transport === 'stdio' ? { command: form.command } : { url: form.url }),
      }
      const r = await fetch('/api/user/mcp/servers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || 'Add failed')
        return
      }
      setAdding(false)
      setForm({ name: '', transport: 'stdio', command: '', url: '' })
      load()
    } catch { setError('Network error') }
    finally { setSaving(false) }
  }

  const deleteServer = async (serverId) => {
    setError(null)
    try {
      const r = await fetch(`/api/user/mcp/servers/${encodeURIComponent(serverId)}`, { method: 'DELETE' })
      if (!r.ok) { setError('Delete failed'); return }
      load()
    } catch { setError('Delete failed') }
  }

  if (!loaded) {
    return (
      <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 13, color: 'var(--text-dim)' }}>
          MCP servers available to your tasks (shared by admin + your private servers).
        </span>
        <button onClick={load} disabled={loading}>{loading ? 'Loading…' : 'View servers'}</button>
      </div>
    )
  }

  const shared   = data?.shared  || []
  const private_ = data?.private || []

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Shared servers */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 8 }}>
          Admin-granted
        </div>
        {shared.length === 0 ? (
          <p style={{ fontSize: 13, color: 'var(--text-mute)' }}>No shared servers available.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {shared.map(s => (
              <div key={s.server_id} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '6px 0', borderBottom: '1px solid var(--border)',
              }}>
                <div>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{s.name}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-mute)', marginLeft: 8 }}>
                    {s.transport}
                  </span>
                  {s.requires_credential && (
                    <span style={{ fontSize: 11, color: 'var(--warn-fg)', marginLeft: 6 }}>
                      ⚠ requires <code>{s.requires_credential}</code> in vault
                    </span>
                  )}
                </div>
                <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-mute)' }}>
                  {s.server_id}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Private servers */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 8 }}>
          My private servers
        </div>
        {private_.length === 0 ? (
          <p style={{ fontSize: 13, color: 'var(--text-mute)' }}>No private servers added yet.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {private_.map(s => (
              <div key={s.server_id} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '6px 0', borderBottom: '1px solid var(--border)',
              }}>
                <div>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{s.name || s.server_id}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-mute)', marginLeft: 8 }}>{s.transport}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-mute)', marginLeft: 8 }}>
                    {s.command || s.url || ''}
                  </span>
                </div>
                <button
                  className="danger"
                  style={{ fontSize: 11, padding: '3px 10px' }}
                  onClick={() => deleteServer(s.server_id)}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add private server */}
      {adding ? (
        <form onSubmit={addServer} style={{ display: 'flex', flexDirection: 'column', gap: 10, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <div className="field">
            <label>Name</label>
            <input
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="e.g. My local filesystem"
              required
              autoFocus
            />
          </div>
          <div className="field">
            <label>Transport</label>
            <select value={form.transport} onChange={e => setForm(f => ({ ...f, transport: e.target.value }))}>
              <option value="stdio">stdio</option>
              <option value="http">http</option>
            </select>
          </div>
          {form.transport === 'stdio' ? (
            <div className="field">
              <label>Command</label>
              <input
                value={form.command}
                onChange={e => setForm(f => ({ ...f, command: e.target.value }))}
                placeholder="e.g. uvx orchid-mcp-filesystem /path"
                required
              />
            </div>
          ) : (
            <div className="field">
              <label>URL</label>
              <input
                type="url"
                value={form.url}
                onChange={e => setForm(f => ({ ...f, url: e.target.value }))}
                placeholder="https://my-mcp-server.example.com/mcp"
                required
              />
            </div>
          )}
          {error && <p style={{ color: 'var(--error-fg)', fontSize: 13 }}>{error}</p>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="submit" className="primary" disabled={saving}>{saving ? 'Adding…' : 'Add server'}</button>
            <button type="button" onClick={() => { setAdding(false); setError(null) }}>Cancel</button>
          </div>
        </form>
      ) : (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          {error && <p style={{ color: 'var(--error-fg)', fontSize: 12, margin: 0 }}>{error}</p>}
          <button onClick={() => setAdding(true)} style={{ marginLeft: 'auto' }}>+ Add private server</button>
        </div>
      )}
    </div>
  )
}
