function shortenPath(path) {
  if (!path) return ''
  return path.replace(/^\/home\/[^/]+/, '~')
}

function projectStatusIndicator(p) {
  if (p.running) return { dot: '🔵', label: 'running' }
  if (p.error) return { dot: '🔴', label: 'error' }
  if (!p.task_counts || p.task_counts.todo === 0) return { dot: '⚫', label: 'empty' }
  return { dot: '🟢', label: 'idle' }
}

export default function ProjectSwitcher({ projects, activeId, onSelect, newProjectIds }) {
  if (!projects.length) {
    return <div className="empty-state" style={{ padding: '20px 16px' }}>No projects</div>
  }

  return (
    <div>
      <div style={{ padding: '8px 16px 4px', fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
        Projects
      </div>
      {projects.map(p => {
        const { dot } = projectStatusIndicator(p)
        const isNew = newProjectIds && newProjectIds.has(p.id)
        const shortPath = shortenPath(p.path)
        return (
          <div
            key={p.id}
            className={`project-item ${p.id === activeId ? 'active' : ''}`}
            onClick={() => onSelect(p.id)}
          >
            <div className="project-name" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontSize: 10 }}>{dot}</span>
              {p.name || p.id}
              {isNew && (
                <span style={{
                  fontSize: 9,
                  background: 'var(--accent)',
                  color: '#fff',
                  borderRadius: 8,
                  padding: '1px 5px',
                  marginLeft: 2,
                  fontWeight: 600,
                  letterSpacing: '0.3px',
                }}>
                  NEW
                </span>
              )}
              {p.persistent && p.persistent.auto_run && (
                <span style={{ fontSize: 9, color: 'var(--text-dim)' }} title="Auto-run enabled">⟳</span>
              )}
            </div>
            {shortPath && (
              <div className="project-path" title={p.path}>{shortPath}</div>
            )}
            {p.task_counts && (
              <div className="project-counts">
                {p.task_counts.in_progress > 0 && <span style={{ color: '#79b8ff' }}>{p.task_counts.in_progress} running · </span>}
                {p.task_counts.todo} todo · {p.task_counts.done} done
                {p.task_counts.blocked > 0 && <span style={{ color: 'var(--error)' }}> · {p.task_counts.blocked} blocked</span>}
              </div>
            )}
            {p.last_session && (
              <div className="project-counts" style={{ color: 'var(--text-dim)' }}>
                last: {new Date(p.last_session).toLocaleDateString()}
              </div>
            )}
            {p.error && <div className="project-counts" style={{ color: 'var(--error)' }}>Error loading</div>}
          </div>
        )
      })}
    </div>
  )
}
