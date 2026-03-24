import { useState, useEffect } from 'react'

export default function ProjectSettings({ projectId }) {
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    fetch(`/api/projects/${projectId}/settings`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { setSettings(d); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [projectId])

  if (loading) return <div className="loading">Loading settings…</div>
  if (error) return <div className="error-msg">Error: {error}</div>
  if (!settings) return null

  return (
    <div className="project-settings-panel">
      <h3 style={{ marginBottom: 16, fontSize: 15 }}>Project Settings</h3>

      <div className="project-settings-file">
        <div className="project-settings-file-header">
          <span>.orchid.yaml</span>
          {settings.orchid_yaml === null && <span style={{ color: 'var(--text-dim)', fontStyle: 'italic' }}>not found</span>}
        </div>
        {settings.orchid_yaml !== null ? (
          <pre>{settings.orchid_yaml}</pre>
        ) : (
          <div style={{ padding: '12px', color: 'var(--text-dim)', fontSize: 12 }}>
            No .orchid.yaml found in project root.
          </div>
        )}
      </div>

      <div className="project-settings-file">
        <div className="project-settings-file-header">
          <span>.env</span>
          {settings.env === null && <span style={{ color: 'var(--text-dim)', fontStyle: 'italic' }}>not found</span>}
          {settings.env !== null && (
            <span style={{ fontSize: 10, color: 'var(--warning)' }}>sensitive values redacted</span>
          )}
        </div>
        {settings.env !== null ? (
          <pre>{settings.env}</pre>
        ) : (
          <div style={{ padding: '12px', color: 'var(--text-dim)', fontSize: 12 }}>
            No .env file found in project root.
          </div>
        )}
      </div>
    </div>
  )
}
