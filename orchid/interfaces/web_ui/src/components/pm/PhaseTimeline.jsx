import { useState, useEffect } from 'react'

const PHASE_ORDER = ['NEW', 'DISCUSSING', 'REQUIREMENTS', 'PLANNING', 'READY', 'EXECUTING', 'COMPLETE']
const PHASE_COLOR = {
  NEW:          '#8b949e',
  DISCUSSING:   '#388bfd',
  REQUIREMENTS: '#a5d6ff',
  PLANNING:     '#e3b341',
  READY:        '#f0883e',
  EXECUTING:    '#56d364',
  COMPLETE:     '#3fb950',
}

function formatDuration(ms) {
  if (ms < 60000) return `${Math.round(ms / 1000)}s`
  if (ms < 3600000) return `${Math.round(ms / 60000)}m`
  if (ms < 86400000) return `${(ms / 3600000).toFixed(1)}h`
  return `${(ms / 86400000).toFixed(1)}d`
}

export default function PhaseTimeline({ projectId }) {
  const [lifecycle, setLifecycle] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!projectId) return
    setLoading(true)
    fetch(`/api/projects/${projectId}/lifecycle`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { setLifecycle(d); setError(null) })
      .catch(err => setError(String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <div className="loading" style={{ padding: 12 }}>Loading lifecycle…</div>
  if (error) return <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: 12, fontStyle: 'italic' }}>Lifecycle data unavailable.</div>
  if (!lifecycle || lifecycle.phase === 'NEW') {
    return <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: 12, fontStyle: 'italic' }}>No V2 lifecycle data. Start a planning session to track phases.</div>
  }

  const currentIdx = PHASE_ORDER.indexOf(lifecycle.phase)
  const createdAt = lifecycle.created_at ? new Date(lifecycle.created_at) : null
  const lastActivity = lifecycle.last_activity ? new Date(lifecycle.last_activity) : new Date()
  const totalMs = createdAt ? (lastActivity - createdAt) : 0

  // Distribute elapsed time evenly across completed phases (approximation — no per-phase timing stored)
  const completedPhases = PHASE_ORDER.slice(0, currentIdx + 1)
  const msPerPhase = completedPhases.length > 0 && totalMs > 0 ? totalMs / completedPhases.length : 0

  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 12 }}>
        Created: {createdAt ? createdAt.toLocaleString() : '—'} ·
        Last activity: {lastActivity.toLocaleString()} ·
        Total: {totalMs > 0 ? formatDuration(totalMs) : '—'}
      </div>
      <div className="phase-timeline-bars">
      <div style={{ display: 'flex', gap: 3, alignItems: 'stretch', height: 52 }}>
        {PHASE_ORDER.map((phase, idx) => {
          const reached = idx <= currentIdx
          const isCurrent = idx === currentIdx
          const isFuture = idx > currentIdx

          return (
            <div
              key={phase}
              title={reached && msPerPhase > 0 ? `~${formatDuration(msPerPhase)}` : phase}
              style={{
                flex: reached ? 1 : 0.4,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                background: isFuture ? 'var(--bg)' : PHASE_COLOR[phase],
                border: isCurrent ? '2px solid #fff' : '1px solid var(--border)',
                borderRadius: 4,
                opacity: isFuture ? 0.4 : 1,
                transition: 'flex 0.3s',
                minWidth: 60,
                cursor: 'default',
                position: 'relative',
              }}
            >
              <span style={{
                fontSize: 10,
                fontWeight: isCurrent ? 700 : 400,
                color: isFuture ? 'var(--text-dim)' : '#0d1117',
                textAlign: 'center',
                padding: '0 4px',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                maxWidth: '100%',
              }}>
                {phase}
              </span>
              {reached && msPerPhase > 0 && (
                <span style={{ fontSize: 9, color: isFuture ? 'var(--text-dim)' : 'rgba(0,0,0,0.6)', marginTop: 2 }}>
                  ~{formatDuration(msPerPhase)}
                </span>
              )}
            </div>
          )
        })}
      </div>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 8 }}>
        Current phase: <strong style={{ color: PHASE_COLOR[lifecycle.phase] }}>{lifecycle.phase}</strong>
        {lifecycle.current_milestone && (
          <span> · Milestone: {lifecycle.current_milestone}</span>
        )}
      </div>
    </div>
  )
}
