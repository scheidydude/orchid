import { useState } from 'react'
import MilestoneProgress from './MilestoneProgress.jsx'
import DependencyGraph from './DependencyGraph.jsx'
import SessionBurndown from './SessionBurndown.jsx'
import PhaseTimeline from './PhaseTimeline.jsx'
import TaskTiming from './TaskTiming.jsx'

function Section({ title, defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div style={{ marginBottom: 20, border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 16px',
          background: 'var(--bg-panel)',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
          fontWeight: 600,
          fontSize: 14,
          color: 'var(--text)',
          borderBottom: open ? '1px solid var(--border)' : 'none',
        }}
      >
        <span>{title}</span>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div style={{ padding: 16 }}>
          {children}
        </div>
      )}
    </div>
  )
}

export default function PMDashboard({ projectId }) {
  if (!projectId) {
    return <div className="empty-state">Select a project to view the PM Dashboard.</div>
  }

  return (
    <div className="pm-dashboard" style={{ maxWidth: 960, margin: '0 auto' }}>
      <Section title="🏁 Milestone Progress">
        <MilestoneProgress projectId={projectId} />
      </Section>

      <Section title="🔗 Dependency Graph">
        <DependencyGraph projectId={projectId} />
      </Section>

      <Section title="📊 Session Burndown">
        <SessionBurndown projectId={projectId} />
      </Section>

      <Section title="📅 Phase Timeline">
        <PhaseTimeline projectId={projectId} />
      </Section>

      <Section title="⏱ Task Timing">
        <TaskTiming projectId={projectId} />
      </Section>
    </div>
  )
}
