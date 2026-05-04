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
    console.log('advance clicked, projectId=', projectId)
    fetch(`/api/projects/${projectId}/advance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true }),
    })
      .then(r => { console.log('advance response:', r.status); return r.ok ? r.json() : r.json().then(d => Promise.reject(d.detail)) })
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

      {advancing && phase !== 'NEW' && (
        <div className="advance-progress">
          {advanceLog.map((l, i) => (
            <div key={i} className="advance-log-entry">{l}</div>
          ))}
          <div className="advance-spinner">⏳</div>
        </div>
      )}

      {(phase === 'NEW' || phase === 'DISCUSSING') && (
        <DiscussionPanel
          projectId={projectId}
          onReadyToAdvance={doAdvance}
          onReset={loadLifecycle}
          advancing={advancing}
          advanceLog={advanceLog}
        />
      )}

      {!advancing && phase !== 'NEW' && (
        <>

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
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <ArtifactPanel
                projectId={projectId}
                currentPhase={phase}
                onRegenerate={doAdvance}
              />
              <div style={{ flexShrink: 0 }}>
                <ApprovalPanel
                  projectId={projectId}
                  onApproved={handleApproved}
                  onKeepReviewing={() => {}}
                />
              </div>
            </div>
          )}

          {phase === 'EXECUTING' && (
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <ArtifactPanel
                projectId={projectId}
                currentPhase={phase}
                readOnly
              />
              <div style={{ flexShrink: 0, padding: '8px 16px', borderTop: '1px solid var(--border)' }}>
                <p style={{ color: 'var(--text-dim)', margin: 0, fontSize: 13 }}>
                  ⚡ Tasks are running.
                  {onSwitchToTasks && (
                    <> <button className="link-btn" onClick={onSwitchToTasks} style={{ color: 'var(--accent)' }}>Go to Tasks →</button></>
                  )}
                  {' '}
                  <button
                    className="link-btn"
                    style={{ color: 'var(--text-dim)' }}
                    onClick={() => {
                      fetch(`/api/projects/${projectId}/lifecycle/validate-executing`, { method: 'POST' })
                        .then(r => r.json())
                        .then(d => {
                          if (d.advanced) loadLifecycle()
                          else alert(d.reason || 'Tasks still pending')
                        })
                        .catch(e => alert(String(e)))
                    }}
                  >Check completion</button>
                </p>
              </div>
            </div>
          )}

          {phase === 'COMPLETE' && (
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <ArtifactPanel
                projectId={projectId}
                currentPhase={phase}
                readOnly
              />
              <div style={{ flexShrink: 0, padding: '8px 16px', borderTop: '1px solid var(--border)' }}>
                <p style={{ color: 'var(--text-dim)', margin: 0, fontSize: 13 }}>
                  🏁 All milestones finished.{' '}
                  <button
                    className="link-btn"
                    onClick={doAdvance}
                    style={{ color: 'var(--accent-2)' }}
                  >
                    Start new milestone?
                  </button>
                </p>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
