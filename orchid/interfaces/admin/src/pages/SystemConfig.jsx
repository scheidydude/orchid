import { useState, useEffect } from 'react'

// ── Toggle row ────────────────────────────────────────────────────────────────

function ToggleRow({ label, hint, value, onChange, saving }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', padding: '12px 0', borderBottom: '1px solid var(--border)' }}>
      <div>
        <div style={{ fontWeight: 500 }}>{label}</div>
        {hint && <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 3 }}>{hint}</div>}
      </div>
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', flexShrink: 0, marginLeft: 16 }}>
        <input
          type="checkbox"
          checked={!!value}
          onChange={e => onChange(e.target.checked)}
          disabled={saving}
        />
        <span style={{ fontSize: 12, color: value ? 'var(--success-fg)' : 'var(--text-mute)' }}>
          {value ? 'enabled' : 'disabled'}
        </span>
      </label>
    </div>
  )
}

// ── Number row ────────────────────────────────────────────────────────────────

function NumberRow({ label, hint, value, onChange, saving, placeholder = '0 = unlimited' }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal]         = useState(String(value ?? 0))

  useEffect(() => { setVal(String(value ?? 0)) }, [value])

  const commit = () => {
    const n = parseFloat(val)
    if (!isNaN(n) && n >= 0) onChange(n)
    setEditing(false)
  }

  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', padding: '12px 0', borderBottom: '1px solid var(--border)' }}>
      <div>
        <div style={{ fontWeight: 500 }}>{label}</div>
        {hint && <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 3 }}>{hint}</div>}
      </div>
      <div style={{ marginLeft: 16, flexShrink: 0 }}>
        {editing ? (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input
              type="number" min={0} step="0.01"
              value={val}
              onChange={e => setVal(e.target.value)}
              style={{ width: 100 }}
              autoFocus
              onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false) }}
            />
            <button style={{ padding: '3px 8px', fontSize: 11 }} disabled={saving} onClick={commit}>✓</button>
            <button className="ghost" style={{ padding: '3px 8px', fontSize: 11 }} onClick={() => setEditing(false)}>✕</button>
          </div>
        ) : (
          <span
            style={{ cursor: 'pointer', borderBottom: '1px dashed var(--border)', fontSize: 13 }}
            title="Click to edit"
            onClick={() => setEditing(true)}
          >
            {value === 0 || value == null
              ? <em style={{ color: 'var(--text-mute)' }}>unlimited</em>
              : value}
          </span>
        )}
      </div>
    </div>
  )
}

// ── System Config page ────────────────────────────────────────────────────────

export default function SystemConfig() {
  const [cfg, setCfg]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving]   = useState(false)
  const [error, setError]     = useState('')
  const [success, setSuccess] = useState('')

  useEffect(() => {
    setLoading(true)
    fetch('/api/admin/config')
      .then(r => r.json())
      .then(d => { setCfg(d); setLoading(false) })
      .catch(() => { setError('Failed to load config'); setLoading(false) })
  }, [])

  const save = async (key, value) => {
    setSaving(true); setError(''); setSuccess('')
    try {
      const r = await fetch('/api/admin/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setError(d.detail || 'Save failed'); return }
      setSuccess('Saved')
      setTimeout(() => setSuccess(''), 2000)
      // Optimistic local update
      const [section, subkey] = key.split('.')
      setCfg(prev => ({
        ...prev,
        [section]: { ...(prev[section] || {}), [subkey]: value },
      }))
    } catch { setError('Network error') }
    finally { setSaving(false) }
  }

  if (loading) return (
    <div className="page">
      <div style={{ display: 'flex', gap: 10, padding: 24, color: 'var(--text-dim)', alignItems: 'center' }}>
        <div className="spinner" /> Loading…
      </div>
    </div>
  )

  const mu = cfg?.multi_user || {}
  const web = cfg?.web || {}

  return (
    <div className="page">
      <div className="section-header">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700 }}>System Config</h2>
          <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 4 }}>
            Multi-user settings. Changes written to ~/.config/orchid/config.yaml.
          </p>
        </div>
        {success && <span style={{ fontSize: 12, color: 'var(--success-fg)' }}>✓ {success}</span>}
      </div>

      {error && <p style={{ color: 'var(--error-fg)', marginBottom: 14 }}>{error}</p>}

      {/* User features */}
      <div className="card" style={{ marginBottom: 20 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 0, color: 'var(--text-dim)' }}>User features</h3>
        <ToggleRow
          label="Allow user MCP servers"
          hint="Users can add private MCP server definitions in their settings."
          value={web.allow_user_mcp}
          onChange={v => save('web.allow_user_mcp', v)}
          saving={saving}
        />
        <ToggleRow
          label="Allow user projects"
          hint="Users can create and manage their own Orchid projects."
          value={web.allow_user_projects}
          onChange={v => save('web.allow_user_projects', v)}
          saving={saving}
        />
      </div>

      {/* Default quotas */}
      <div className="card" style={{ marginBottom: 20 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 0, color: 'var(--text-dim)' }}>Default quotas</h3>
        <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 8 }}>
          Applied to newly-created users. Existing users are not affected.
        </div>
        <NumberRow
          label="Default LLM budget (USD)"
          hint="0 = unlimited. Set to e.g. 10.0 to give new users a $10 spending cap."
          value={mu.default_budget_usd}
          onChange={v => save('multi_user.default_budget_usd', v)}
          saving={saving}
        />
        <NumberRow
          label="Default CPU budget (seconds/day)"
          hint="0 = unlimited. Daily wall-clock cap for scheduled task execution."
          value={mu.default_cpu_seconds}
          onChange={v => save('multi_user.default_cpu_seconds', v)}
          saving={saving}
        />
      </div>

      {/* Read-only info */}
      <div className="card">
        <h3 style={{ fontSize: 14, fontWeight: 700, marginBottom: 8, color: 'var(--text-dim)' }}>Read-only info</h3>
        <div style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div>
            <strong>Credential encryption:</strong>{' '}
            <code>{mu.credential_encryption || 'fernet'}</code>
            <span style={{ color: 'var(--text-mute)', marginLeft: 8 }}>
              Change by editing ~/.config/orchid/config.yaml and restarting.
            </span>
          </div>
          <div>
            <strong>Portals:</strong>{' '}
            User portal: <code>/app/</code> · Admin console: <code>/admin/</code>
          </div>
        </div>
      </div>
    </div>
  )
}
