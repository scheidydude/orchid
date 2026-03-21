import { useState, useEffect } from 'react'

const PROJECT_TYPES = [
  { value: '',     label: 'Auto-detect' },
  { value: 'web',  label: '🌐 Web app' },
  { value: 'ai',   label: '🤖 AI project' },
  { value: 'tool', label: '🔧 CLI / tool' },
  { value: 'game', label: '🎮 Game' },
]

function slugify(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40)
}

export default function NewProjectWizard({ onCreated, onClose }) {
  const [step, setStep] = useState(1)
  const [description, setDescription] = useState('')
  const [name, setName] = useState('')
  const [projectType, setProjectType] = useState('')
  const [suggestedPath, setSuggestedPath] = useState('')
  const [customPath, setCustomPath] = useState('')
  const [gitInit, setGitInit] = useState(true)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState(null)
  const [progress, setProgress] = useState([])
  const [machineProfile, setMachineProfile] = useState(null)

  // Auto-slugify description → name
  useEffect(() => {
    if (description && !name) setName(slugify(description))
  }, [description])

  // Load machine profile for path suggestions
  useEffect(() => {
    fetch('/api/machine-profile').then(r => r.json()).then(setMachineProfile).catch(() => {})
  }, [])

  // Fetch suggested path when moving to step 2
  useEffect(() => {
    if (step === 2 && name) {
      fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description, project_type: projectType || null, confirm_path: false }),
      })
        .then(r => r.json())
        .then(d => {
          setSuggestedPath(d.suggested_path || '')
          if (!customPath) setCustomPath(d.suggested_path || '')
        })
        .catch(() => {})
    }
  }, [step])

  const handleCreate = () => {
    setCreating(true)
    setCreateError(null)
    setProgress(['Creating directory…'])

    // Determine base_dir from custom path: strip project name from end if present
    let base_dir = null
    if (customPath) {
      const pathObj = customPath.endsWith('/' + name)
        ? customPath.slice(0, -name.length - 1)
        : customPath
      base_dir = pathObj || null
    }

    fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        description,
        project_type: projectType || null,
        base_dir,
        confirm_path: true,
      }),
    })
      .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail)))
      .then(d => {
        setProgress(prev => [...prev, 'Git init…', 'Orchid init…', 'Done!'])
        setTimeout(() => {
          setCreating(false)
          onCreated && onCreated(d.project_id, d.path)
        }, 800)
      })
      .catch(e => {
        setCreateError(String(e))
        setCreating(false)
        setProgress([])
      })
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal wizard-modal" onClick={e => e.stopPropagation()}>
        <div className="wizard-header">
          <h3>New Project</h3>
          <div className="wizard-steps">
            {[1, 2, 3, 4].map(s => (
              <div key={s} className={`wizard-step-dot ${step >= s ? 'active' : ''}`} />
            ))}
          </div>
          <button className="icon-btn" onClick={onClose} title="Close">✕</button>
        </div>

        {step === 1 && (
          <div className="wizard-body">
            <h4>What do you want to build?</h4>
            <div className="form-group">
              <label>Description</label>
              <textarea
                placeholder="A web app that lets users save and organise bookmarks…"
                value={description}
                onChange={e => setDescription(e.target.value)}
                rows={3}
                autoFocus
              />
            </div>
            <div className="form-group">
              <label>Project name</label>
              <input
                placeholder="my-project"
                value={name}
                onChange={e => setName(slugify(e.target.value) || e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Project type</label>
              <select value={projectType} onChange={e => setProjectType(e.target.value)}>
                {PROJECT_TYPES.map(t => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div className="wizard-nav">
              <button onClick={onClose}>Cancel</button>
              <button
                className="primary"
                onClick={() => setStep(2)}
                disabled={!name || !description}
              >
                Next →
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="wizard-body">
            <h4>Confirm Location</h4>
            <div className="form-group">
              <label>Project path</label>
              <input
                value={customPath || suggestedPath}
                onChange={e => setCustomPath(e.target.value)}
                placeholder={suggestedPath || 'Loading…'}
              />
              {suggestedPath && customPath !== suggestedPath && (
                <button
                  className="link-btn"
                  onClick={() => setCustomPath(suggestedPath)}
                  style={{ fontSize: 11, marginTop: 4 }}
                >
                  Reset to suggested
                </button>
              )}
            </div>
            {machineProfile && (
              <div className="path-hint">
                Based on your machine profile{projectType ? ` (${projectType} projects)` : ''}.
                Edit <code>~/.config/orchid/machine-profile.yaml</code> to change defaults.
              </div>
            )}
            <div className="wizard-nav">
              <button onClick={() => setStep(1)}>← Back</button>
              <button className="primary" onClick={() => setStep(3)}>Next →</button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="wizard-body">
            <h4>Options</h4>
            <div className="form-group">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={gitInit}
                  onChange={e => setGitInit(e.target.checked)}
                />
                Initialise git repository
              </label>
            </div>
            <div className="create-summary">
              <div><strong>Name:</strong> {name}</div>
              <div><strong>Path:</strong> {customPath || suggestedPath}</div>
              {projectType && <div><strong>Type:</strong> {projectType}</div>}
            </div>
            <div className="wizard-nav">
              <button onClick={() => setStep(2)}>← Back</button>
              <button className="primary" onClick={() => { setStep(4); handleCreate() }}>
                Create Project
              </button>
            </div>
          </div>
        )}

        {step === 4 && (
          <div className="wizard-body">
            <h4>{createError ? 'Creation failed' : creating ? 'Creating…' : 'Done!'}</h4>
            {createError ? (
              <div className="error-msg">{createError}</div>
            ) : (
              <div className="progress-list">
                {progress.map((p, i) => (
                  <div key={i} className="progress-item">
                    <span className="progress-check">{creating && i === progress.length - 1 ? '⏳' : '✓'}</span>
                    {p}
                  </div>
                ))}
              </div>
            )}
            {createError && (
              <div className="wizard-nav">
                <button onClick={() => { setStep(3); setCreateError(null) }}>← Back</button>
                <button onClick={onClose}>Close</button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
