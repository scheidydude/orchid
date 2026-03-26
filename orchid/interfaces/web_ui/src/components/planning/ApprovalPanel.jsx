import { useState, useEffect } from 'react'

const STORAGE_KEY = 'orchid_approval_panel_collapsed'

function countLines(text, prefix) {
  if (!text) return 0
  return text.split('\n').filter(l => l.startsWith(prefix)).length
}

function countTasks(text) {
  if (!text) return 0
  return (text.match(/- \[ \]/g) || []).length
}

export default function ApprovalPanel({ projectId, onApproved, onKeepReviewing }) {
  const [artifacts, setArtifacts] = useState({})
  const [lifecycle, setLifecycle] = useState(null)
  const [autoFuture, setAutoFuture] = useState(false)
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      return stored === null ? true : stored === 'true'
    } catch {
      return true
    }
  })

  useEffect(() => {
    Promise.all([
      fetch(`/api/projects/${projectId}/artifacts`).then(r => r.json()),
      fetch(`/api/projects/${projectId}/lifecycle`).then(r => r.json()),
    ]).then(([a, lc]) => {
      setArtifacts(a)
      setLifecycle(lc)
    }).catch(() => {})
  }, [projectId])

  const toggleCollapsed = () => {
    const next = !collapsed
    setCollapsed(next)
    try { localStorage.setItem(STORAGE_KEY, String(next)) } catch {}
  }

  const reqCount = countLines(artifacts.requirements?.content, 'FR-')
  const nfrCount = countLines(artifacts.requirements?.content, 'NFR-')
  const taskCount = countTasks(artifacts.tasks?.content)
  const milestoneCount = countLines(artifacts.milestones?.content, '## Milestone')

  const extractStack = (text) => {
    if (!text) return ''
    const lines = text.split('\n')
    const stackLines = []
    let inStack = false
    for (const line of lines) {
      if (line.includes('## Tech Stack')) { inStack = true; continue }
      if (inStack && line.startsWith('## ')) break
      if (inStack && line.trim().startsWith('- ')) stackLines.push(line.trim().slice(2))
    }
    return stackLines.slice(0, 4).join(', ')
  }

  const techStack = extractStack(artifacts.architecture?.content)

  const doApprove = () => {
    setApproving(true)
    setError(null)
    fetch(`/api/projects/${projectId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_future: autoFuture }),
    })
      .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail)))
      .then(d => {
        setApproving(false)
        setConfirmOpen(false)
        onApproved && onApproved(d.phase)
      })
      .catch(e => {
        setError(String(e))
        setApproving(false)
        setConfirmOpen(false)
      })
  }

  return (
    <div className="approval-panel">
      {/* Always-visible header row */}
      <div className="approval-header" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 16 }}>✅</span>
        <span style={{ fontWeight: 600, flex: 1 }}>Ready to Execute</span>
        {collapsed && (
          <button
            className="primary"
            onClick={() => setConfirmOpen(true)}
            disabled={approving}
            style={{ marginRight: 4 }}
          >
            {approving ? 'Approving…' : 'Approve & Start'}
          </button>
        )}
        <button
          className="btn-icon"
          onClick={toggleCollapsed}
          title={collapsed ? 'Show details' : 'Hide details'}
          style={{ padding: '2px 8px', fontSize: 13, minWidth: 60 }}
        >
          {collapsed ? '▲ More' : '▼ Less'}
        </button>
      </div>

      {/* Expandable content */}
      <div
        className={`approval-body${collapsed ? ' approval-body--collapsed' : ''}`}
        style={{
          overflow: 'hidden',
          maxHeight: collapsed ? 0 : 500,
          transition: 'max-height 0.25s ease',
        }}
      >
        <div style={{ paddingTop: 12 }}>
          <div className="approval-summary">
            <div className="approval-item">
              <span className="approval-check">✅</span>
              <span>{taskCount} tasks across {milestoneCount} milestones</span>
            </div>
            {artifacts.requirements?.exists && (
              <div className="approval-item">
                <span className="approval-check">✅</span>
                <span>REQUIREMENTS.md — {reqCount} functional, {nfrCount} non-functional requirements</span>
              </div>
            )}
            {artifacts.architecture?.exists && (
              <div className="approval-item">
                <span className="approval-check">✅</span>
                <span>ARCHITECTURE.md{techStack ? ` — ${techStack}` : ''}</span>
              </div>
            )}
            {artifacts.milestones?.exists && (
              <div className="approval-item">
                <span className="approval-check">✅</span>
                <span>MILESTONES.md — {milestoneCount} phases</span>
              </div>
            )}
          </div>

          {error && <div className="error-msg" style={{ margin: '12px 0' }}>{error}</div>}

          <div className="approval-options">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={autoFuture}
                onChange={e => setAutoFuture(e.target.checked)}
              />
              Auto-approve future gates in this project
            </label>
          </div>

          <div className="approval-actions">
            <button className="primary" onClick={() => setConfirmOpen(true)} disabled={approving}>
              {approving ? 'Approving…' : 'Approve & Start Execution'}
            </button>
            {onKeepReviewing && (
              <button onClick={onKeepReviewing}>Keep Reviewing</button>
            )}
          </div>
        </div>
      </div>

      {confirmOpen && (
        <div className="modal-overlay" onClick={() => setConfirmOpen(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Confirm Approval</h3>
            <p style={{ marginTop: 8, color: 'var(--text-dim)' }}>
              This will advance the project to EXECUTING phase and make the task list available
              for autonomous runs. You can still edit tasks before starting.
            </p>
            {autoFuture && (
              <p style={{ marginTop: 8, fontSize: 12, color: 'var(--warning)' }}>
                Future gates will be auto-approved for this project.
              </p>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end' }}>
              <button onClick={() => setConfirmOpen(false)}>Cancel</button>
              <button className="primary" onClick={doApprove} disabled={approving}>
                {approving ? 'Approving…' : 'Confirm Approve'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
