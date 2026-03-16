export default function ProjectSwitcher({ projects, activeId, onSelect }) {
  if (!projects.length) {
    return <div className="empty-state" style={{ padding: '20px 16px' }}>No projects</div>
  }

  return (
    <div>
      <div style={{ padding: '8px 16px 4px', fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
        Projects
      </div>
      {projects.map(p => (
        <div
          key={p.id}
          className={`project-item ${p.id === activeId ? 'active' : ''}`}
          onClick={() => onSelect(p.id)}
        >
          <div className="project-name">
            {p.running && <span className="project-running-dot" />}
            {p.name || p.id}
          </div>
          {p.task_counts && (
            <div className="project-counts">
              {p.task_counts.in_progress > 0 && <span style={{ color: '#79b8ff' }}>{p.task_counts.in_progress} running · </span>}
              {p.task_counts.todo} todo · {p.task_counts.done} done
              {p.task_counts.blocked > 0 && <span style={{ color: 'var(--error)' }}> · {p.task_counts.blocked} blocked</span>}
            </div>
          )}
          {p.error && <div className="project-counts" style={{ color: 'var(--error)' }}>Error loading</div>}
        </div>
      ))}
    </div>
  )
}
