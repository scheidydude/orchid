import { useState, useEffect } from 'react'

const ARTIFACT_TABS = [
  { key: 'requirements', label: 'Requirements' },
  { key: 'architecture', label: 'Architecture' },
  { key: 'milestones',   label: 'Milestones'   },
  { key: 'tasks',        label: 'tasks.md'     },
]

function ArtifactView({ projectId, artifact, artifactKey, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const startEdit = () => {
    setEditContent(artifact.content || '')
    setEditing(true)
  }

  const cancelEdit = () => {
    setEditing(false)
    setSaveError(null)
  }

  const save = () => {
    setSaving(true)
    setSaveError(null)
    fetch(`/api/projects/${projectId}/artifacts/${artifactKey}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: editContent }),
    })
      .then(r => r.ok ? r.json() : Promise.reject('Save failed'))
      .then(() => {
        setSaving(false)
        setEditing(false)
        onSaved()
      })
      .catch(e => {
        setSaveError(String(e))
        setSaving(false)
      })
  }

  if (!artifact.exists) {
    return (
      <div className="artifact-empty">
        <div style={{ color: 'var(--text-dim)', fontSize: 13 }}>Not yet generated</div>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>{artifact.path}</div>
      </div>
    )
  }

  if (editing) {
    return (
      <div className="artifact-editor">
        <div className="artifact-editor-toolbar">
          <button className="primary" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button onClick={cancelEdit} disabled={saving}>Cancel</button>
          {saveError && <span className="error-msg">{saveError}</span>}
        </div>
        <textarea
          className="artifact-textarea"
          value={editContent}
          onChange={e => setEditContent(e.target.value)}
          spellCheck={false}
        />
      </div>
    )
  }

  return (
    <div className="artifact-view">
      <div className="artifact-toolbar">
        <span className="artifact-path">{artifact.path.replace(/^\/home\/[^/]+/, '~')}</span>
        <button onClick={startEdit}>Edit</button>
      </div>
      <pre className="artifact-content">{artifact.content}</pre>
    </div>
  )
}

export default function ArtifactPanel({ projectId, currentPhase, onAdvance, onRegenerate }) {
  const [activeTab, setActiveTab] = useState('requirements')
  const [artifacts, setArtifacts] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [advancing, setAdvancing] = useState(false)

  const load = () => {
    setLoading(true)
    fetch(`/api/projects/${projectId}/artifacts`)
      .then(r => r.json())
      .then(d => { setArtifacts(d); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }

  useEffect(() => { load() }, [projectId])

  const handleAdvance = () => {
    setAdvancing(true)
    onAdvance && onAdvance(activeTab)
  }

  if (loading) return <div className="loading" style={{ padding: 16 }}>Loading artifacts…</div>
  if (error) return <div className="error-msg" style={{ padding: 16 }}>{error}</div>

  const canAdvanceToPlanning = currentPhase === 'REQUIREMENTS' &&
    artifacts.requirements?.exists && artifacts.architecture?.exists
  const canAdvanceToReady = (currentPhase === 'PLANNING') &&
    artifacts.milestones?.exists && artifacts.tasks?.exists

  return (
    <div className="artifact-panel">
      <div className="artifact-tab-bar">
        {ARTIFACT_TABS.map(t => (
          <button
            key={t.key}
            className={`panel-tab ${activeTab === t.key ? 'active' : ''}`}
            onClick={() => setActiveTab(t.key)}
          >
            {t.label}
            {artifacts[t.key]?.exists && (
              <span style={{ marginLeft: 5, color: 'var(--success)', fontSize: 10 }}>●</span>
            )}
          </button>
        ))}
      </div>

      <div className="artifact-body">
        <ArtifactView
          projectId={projectId}
          artifact={artifacts[activeTab] || { exists: false, content: null, path: '' }}
          artifactKey={activeTab}
          onSaved={load}
        />
      </div>

      <div className="artifact-actions">
        {onRegenerate && (
          <button onClick={onRegenerate}>Regenerate</button>
        )}
        {canAdvanceToPlanning && (
          <button className="primary" onClick={handleAdvance} disabled={advancing}>
            {advancing ? 'Generating tasks…' : 'Advance to Planning →'}
          </button>
        )}
        {canAdvanceToReady && (
          <button className="primary" onClick={handleAdvance} disabled={advancing}>
            {advancing ? 'Finalising…' : 'Advance to Ready →'}
          </button>
        )}
      </div>
    </div>
  )
}
