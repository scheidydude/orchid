import { useState, useEffect, useCallback } from 'react'
import PhaseIndicator from './PhaseIndicator.jsx'
import DiscussionPanel from './DiscussionPanel.jsx'
import ArtifactPanel from './ArtifactPanel.jsx'
import ApprovalPanel from './ApprovalPanel.jsx'

export default function PlanningTab({ projectId, runStatus, onSwitchToTasks }) {
  const [lifecycle, setLifecycle] = useState(null)
  const [loading, setLoading] = useState(true)
  const [advancing, setAdvancing] = useState(false)
  const [advanceLog, setAdvanceLog] = useState([])
  const [error, setError] = useState(null)

  const loadLifecycle = useCallback(() => {
    fetch(`/api/projects/${projectId}/lifecycle`)
      .then(r => r.json())
      .then(d => { setLifecycle(d); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [projectId])

  useEffect(() => {
    setLoading(true)
    setLifecycle(null)
    setAdvanceLog([])
    setError(null)
    loadLifecycle()
  }, [projectId, loadLifecycle])

  // Listen on main WS for advance_* events
  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/${projectId}`)
    ws.onmessage = e => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'advance_status') {
          setAdvanceLog(prev => [...prev, msg.data.status])
        } else if (msg.type === 'advance_artifact') {
          setAdvanceLog(prev => [...prev, `✓ Generated ${msg.data.name}`])
        } else if (msg.type === 'advance_done') {
          setAdvancing(false)
          setAdvanceLog([])
          loadLifecycle()
        }
      } catch {}
    }
    return () => ws.close()
  }, [projectId, loadLifecycle])

  const doAdvance = () => {
    setAdvancing(true)
    setAdvanceLog(['Starting…'])
    setError(null)
    fetch(`/api/projects/${projectId}/advance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true }),
    })
      .then(r => r.ok ? r.json() : r.json().then(d => Promise.reject(d.detail)))
      .then(d => {
        setAdvancing(false)
        setAdvanceLog([])
        loadLifecycle()
      })
      .catch(e => {
        setError(String(e))
        setAdvancing(false)
        setAdvanceLog([])
      })
  }

  const handleApproved = (newPhase) => {
    loadLifecycle()
    if (newPhase === 'EXECUTING' && onSwitchToTasks) {
      onSwitchToTasks()
    }
  }

  if (loading) return <div className="loading" style={{ padding: 16 }}>Loading lifecycle…</div>
  if (error) return <div className="error-msg" style={{ padding: 16 }}>{error}</div>
  if (!lifecycle) return null

  const phase = lifecycle.phase

  return (
    <div className="planning-tab">
      <PhaseIndicator currentPhase={phase} />

      {advancing && (
        <div className="advance-progress">
          {advanceLog.map((l, i) => (
            <div key={i} className="advance-log-entry">{l}</div>
          ))}
          <div className="advance-spinner">⏳</div>
        </div>
      )}

      {!advancing && (
        <>
          {(phase === 'NEW' || phase === 'DISCUSSING') && (
            <DiscussionPanel
              projectId={projectId}
              onReadyToAdvance={doAdvance}
            />
          )}

          {phase === 'REQUIREMENTS' && (
            <ArtifactPanel
              projectId={projectId}
              currentPhase={phase}
              onAdvance={doAdvance}
              onRegenerate={doAdvance}
            />
          )}

          {phase === 'PLANNING' && (
            <ArtifactPanel
              projectId={projectId}
              currentPhase={phase}
              onAdvance={doAdvance}
              onRegenerate={doAdvance}
            />
          )}

          {phase === 'READY' && (
            <div>
              <ArtifactPanel
                projectId={projectId}
                currentPhase={phase}
                onRegenerate={doAdvance}
              />
              <div style={{ marginTop: 16 }}>
                <ApprovalPanel
                  projectId={projectId}
                  onApproved={handleApproved}
                  onKeepReviewing={() => {}}
                />
              </div>
            </div>
          )}

          {phase === 'EXECUTING' && (
            <div className="planning-executing">
              <div style={{ fontSize: 40, marginBottom: 12 }}>⚡</div>
              <h3>Project is executing</h3>
              <p style={{ color: 'var(--text-dim)', marginTop: 8 }}>
                {lifecycle.project_name} has {Object.values(lifecycle.artifacts).filter(Boolean).length} artifacts generated
                and tasks are ready to run.
              </p>
              {onSwitchToTasks && (
                <button className="primary" onClick={onSwitchToTasks} style={{ marginTop: 16 }}>
                  Go to Tasks →
                </button>
              )}
            </div>
          )}

          {phase === 'COMPLETE' && (
            <div className="planning-executing">
              <div style={{ fontSize: 40, marginBottom: 12 }}>🏁</div>
              <h3>Project complete</h3>
              <p style={{ color: 'var(--text-dim)', marginTop: 8 }}>
                All milestones finished.{' '}
                <button
                  className="link-btn"
                  onClick={doAdvance}
                  style={{ color: 'var(--accent-2)' }}
                >
                  Start new milestone?
                </button>
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
