const PHASES = [
  { key: 'NEW',          label: 'New',          icon: '🌱' },
  { key: 'DISCUSSING',   label: 'Discuss',       icon: '💬' },
  { key: 'REQUIREMENTS', label: 'Requirements',  icon: '📋' },
  { key: 'PLANNING',     label: 'Planning',      icon: '🗓' },
  { key: 'READY',        label: 'Ready',         icon: '✅' },
  { key: 'EXECUTING',    label: 'Executing',     icon: '⚡' },
  { key: 'COMPLETE',     label: 'Complete',      icon: '🏁' },
]

const PHASE_ORDER = PHASES.map(p => p.key)

export default function PhaseIndicator({ currentPhase, onPhaseClick }) {
  const currentIdx = PHASE_ORDER.indexOf(currentPhase)

  return (
    <div className="phase-indicator">
      {PHASES.map((phase, idx) => {
        const isDone = idx < currentIdx
        const isCurrent = idx === currentIdx
        const isClickable = isDone && onPhaseClick
        return (
          <div key={phase.key} className="phase-step-wrapper">
            <div
              className={`phase-step ${isDone ? 'done' : ''} ${isCurrent ? 'current' : ''} ${isClickable ? 'clickable' : ''}`}
              onClick={() => isClickable && onPhaseClick(phase.key)}
              title={phase.key}
            >
              <span className="phase-icon">{isDone ? '✓' : phase.icon}</span>
              <span className="phase-label">{phase.label}</span>
            </div>
            {idx < PHASES.length - 1 && (
              <div className={`phase-connector ${idx < currentIdx ? 'done' : ''}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}
