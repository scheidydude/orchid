import { useState, useEffect } from 'react'

export default function Settings() {
  const [profile, setProfile] = useState(null)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)
  const [providers, setProviders] = useState([])

  useEffect(() => {
    fetch('/api/machine-profile').then(r => r.json()).then(p => {
      setProfile(p)
      setForm({
        developer_name: p.developer_name || '',
        default_root: p.project_roots?.default || '',
        ai_root: p.project_roots?.type_routing?.ai || '',
        domain: p.infrastructure?.domain || '',
      })
    }).catch(() => {})

    fetch('/api/providers').then(r => r.json()).then(setProviders).catch(() => {})
  }, [])

  const save = () => {
    setSaving(true)
    setError(null)
    const payload = {
      developer_name: form.developer_name,
      project_roots: {
        default: form.default_root,
        type_routing: { ai: form.ai_root },
      },
      infrastructure: { domain: form.domain },
    }
    fetch('/api/machine-profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(r => r.ok ? r.json() : Promise.reject('Save failed'))
      .then(() => {
        setSaving(false)
        setEditing(false)
        setSaved(true)
        setTimeout(() => setSaved(false), 2000)
      })
      .catch(e => { setError(String(e)); setSaving(false) })
  }

  return (
    <div className="settings-panel">
      <h3 style={{ marginBottom: 16 }}>Settings</h3>

      <section className="settings-section">
        <div className="settings-section-header">
          <h4>Machine Profile</h4>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {saved && <span style={{ color: 'var(--success)', fontSize: 12 }}>Saved ✓</span>}
            {!editing && (
              <button onClick={() => setEditing(true)}>Edit</button>
            )}
          </div>
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 12 }}>
          Stored at <code>~/.config/orchid/machine-profile.yaml</code>
        </p>

        {editing ? (
          <div className="settings-form">
            <div className="form-group">
              <label>Developer name</label>
              <input
                value={form.developer_name}
                onChange={e => setForm(f => ({ ...f, developer_name: e.target.value }))}
                placeholder="Your name"
              />
            </div>
            <div className="form-group">
              <label>Default project root</label>
              <input
                value={form.default_root}
                onChange={e => setForm(f => ({ ...f, default_root: e.target.value }))}
                placeholder="~/Documents/Development"
              />
            </div>
            <div className="form-group">
              <label>AI project root</label>
              <input
                value={form.ai_root}
                onChange={e => setForm(f => ({ ...f, ai_root: e.target.value }))}
                placeholder="~/LocalAI"
              />
            </div>
            <div className="form-group">
              <label>Domain (for Traefik routing)</label>
              <input
                value={form.domain}
                onChange={e => setForm(f => ({ ...f, domain: e.target.value }))}
                placeholder="example.com"
              />
            </div>
            {error && <div className="error-msg">{error}</div>}
            <div className="settings-form-actions">
              <button className="primary" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button onClick={() => { setEditing(false); setError(null) }}>Cancel</button>
            </div>
          </div>
        ) : profile ? (
          <div className="settings-display">
            {profile.developer_name && (
              <div className="settings-row">
                <span className="settings-key">Developer</span>
                <span>{profile.developer_name}</span>
              </div>
            )}
            <div className="settings-row">
              <span className="settings-key">Default root</span>
              <span className="settings-path">{profile.project_roots?.default || '—'}</span>
            </div>
            <div className="settings-row">
              <span className="settings-key">AI projects</span>
              <span className="settings-path">{profile.project_roots?.type_routing?.ai || '—'}</span>
            </div>
            <div className="settings-row">
              <span className="settings-key">Backend</span>
              <span>{profile.preferred_stacks?.backend?.primary || '—'}</span>
            </div>
            <div className="settings-row">
              <span className="settings-key">Frontend</span>
              <span>{profile.preferred_stacks?.frontend?.primary || '—'}</span>
            </div>
            <div className="settings-row">
              <span className="settings-key">Database</span>
              <span>{profile.preferred_stacks?.database?.primary || '—'}</span>
            </div>
          </div>
        ) : (
          <div className="loading">Loading…</div>
        )}
      </section>

      <section className="settings-section" style={{ marginTop: 24 }}>
        <h4 style={{ marginBottom: 12 }}>Provider Status</h4>
        {providers.length === 0 ? (
          <div className="loading">Loading providers…</div>
        ) : (
          <div className="provider-list">
            {providers.map(p => (
              <div key={p.name} className="provider-row">
                <span className={`provider-status-dot ${p.available ? 'available' : 'unavailable'}`} />
                <span className="provider-name">{p.name}</span>
                <span className="provider-type" style={{ color: 'var(--text-dim)', fontSize: 12 }}>
                  {p.type}
                </span>
                {!p.available && p.missing && (
                  <span style={{ fontSize: 11, color: 'var(--text-dim)', marginLeft: 'auto' }}>
                    {p.missing}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
