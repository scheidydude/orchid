import { useState } from 'react'

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

function ProjectItem({ p, activeId, onSelect, isNew }) {
  const { dot } = projectStatusIndicator(p)
  const shortPath = shortenPath(p.path)
  return (
    <div
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
}

export default function ProjectSwitcher({ projects, activeId, onSelect, newProjectIds, onToggleActive }) {
  const [activeExpanded, setActiveExpanded] = useState(true)
  const [inactiveExpanded, setInactiveExpanded] = useState(false)

  if (!projects.length) {
    return <div className="empty-state" style={{ padding: '20px 16px' }}>No projects</div>
  }

  const active = projects.filter(p => p.active !== false)
  const inactive = projects.filter(p => p.active === false)

  const renderGroup = (label, items, expanded, onToggle) => (
    <>
      <div className="project-group-header" onClick={onToggle}>
        <span className={`project-group-chevron ${expanded ? '' : 'collapsed'}`}>▼</span>
        <span>{label}</span>
        <span style={{ marginLeft: 'auto', opacity: 0.6 }}>{items.length}</span>
      </div>
      {expanded && items.map(p => (
        <div key={p.id} style={{ position: 'relative' }}>
          <ProjectItem
            p={p}
            activeId={activeId}
            onSelect={onSelect}
            isNew={newProjectIds && newProjectIds.has(p.id)}
          />
          {onToggleActive && (
            <button
              onClick={e => { e.stopPropagation(); onToggleActive(p.id, p.active !== false) }}
              title={p.active !== false ? 'Mark inactive' : 'Mark active'}
              style={{
                position: 'absolute',
                top: 6,
                right: 6,
                fontSize: 9,
                padding: '1px 5px',
                background: 'none',
                border: '1px solid var(--border)',
                borderRadius: 3,
                color: 'var(--text-dim)',
                opacity: 0.6,
              }}
            >
              {p.active !== false ? '⏸' : '▶'}
            </button>
          )}
        </div>
      ))}
    </>
  )

  if (inactive.length === 0) {
    // No inactive projects — just show flat list with a header
    return (
      <div>
        <div style={{ padding: '8px 16px 4px', fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Projects
        </div>
        {projects.map(p => (
          <div key={p.id} style={{ position: 'relative' }}>
            <ProjectItem
              p={p}
              activeId={activeId}
              onSelect={onSelect}
              isNew={newProjectIds && newProjectIds.has(p.id)}
            />
            {onToggleActive && (
              <button
                onClick={e => { e.stopPropagation(); onToggleActive(p.id, true) }}
                title="Mark inactive"
                style={{
                  position: 'absolute',
                  top: 6,
                  right: 6,
                  fontSize: 9,
                  padding: '1px 5px',
                  background: 'none',
                  border: '1px solid var(--border)',
                  borderRadius: 3,
                  color: 'var(--text-dim)',
                  opacity: 0.5,
                }}
              >
                ⏸
              </button>
            )}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div>
      {renderGroup('Active', active, activeExpanded, () => setActiveExpanded(x => !x))}
      {renderGroup('Inactive', inactive, inactiveExpanded, () => setInactiveExpanded(x => !x))}
    </div>
  )
}
