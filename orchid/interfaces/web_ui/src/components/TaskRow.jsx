import { useState } from 'react'

export default function TaskRow({ task, onStatusChange, onRunTask, onSuspend, onResume, running, currentTask, suspended }) {
  const [expanded, setExpanded] = useState(false)

  const statusActions = {
    'TODO':        [['done', 'Mark Done'], ['skipped', '⏭ Skip'], ['blocked', '🔴 Block']],
    'IN_PROGRESS': [['done', 'Mark Done'], ['skipped', '⏭ Skip'], ['blocked', '🔴 Block']],
    'BLOCKED':     [['todo', 'Unblock'], ['done', 'Mark Done'], ['skipped', '⏭ Skip']],
    'DONE':        [['todo', 'Reopen']],
    'CANCELLED':   [['todo', 'Reopen']],
    'SKIPPED':     [['todo', 'Restore']],
  }

  const actions = statusActions[task.status] || []
  const canRun = ['TODO', 'BLOCKED', 'SKIPPED'].includes(task.status)
  const isThisRunning = task.status === 'IN_PROGRESS' && currentTask && currentTask.startsWith(task.id)
  const isThisSuspended = isThisRunning && suspended

  return (
    <div className={`task-row ${expanded ? 'expanded' : ''}`} onClick={() => setExpanded(x => !x)}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1 }}>
        <div className="task-row-header" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="task-id">{task.id}</span>
          <span className={`task-status-badge status-${task.status}`}>{task.status.replace('_', ' ')}</span>
          <span className="task-title">{task.title}</span>
          {task.last_error && <span title={task.last_error} style={{ color: 'var(--error, #ff6b6b)', fontSize: 13, cursor: 'help' }}>⚠</span>}
          {canRun && onRunTask && (
            <button
              onClick={e => { e.stopPropagation(); onRunTask(task.id) }}
              disabled={running}
              title="Run this task now"
              style={{ marginLeft: 'auto', fontSize: 11, padding: '2px 8px', color: '#79b8ff', borderColor: '#1f3a5f', background: '#0d1f35' }}
            >
              ▶ Run
            </button>
          )}
          {isThisRunning && !isThisSuspended && onSuspend && (
            <button
              onClick={e => { e.stopPropagation(); onSuspend(task.id) }}
              title="Pause this task"
              style={{ marginLeft: canRun ? 4 : 'auto', fontSize: 11, padding: '2px 8px', color: '#f0a500', borderColor: '#5a3e00', background: '#2a1e00' }}
            >
              ⏸
            </button>
          )}
          {isThisSuspended && onResume && (
            <button
              onClick={e => { e.stopPropagation(); onResume(task.id) }}
              title="Resume this task"
              style={{ marginLeft: canRun ? 4 : 'auto', fontSize: 11, padding: '2px 8px', color: '#56d364', borderColor: '#1a4a26', background: '#0d2213' }}
            >
              ▶ Resume
            </button>
          )}
        </div>
        <div className="task-meta">
          <span className="tag">type:{task.type}</span>
          <span className="tag">p{task.priority}</span>
          {task.model_override && <span className="tag">model:{task.model_override}</span>}
          {task.depends_on?.length > 0 && <span className="tag">needs:{task.depends_on.join(',')}</span>}
        </div>
        {expanded && (
          <div className="task-detail" onClick={e => e.stopPropagation()}>
            {task.description && <div className="task-detail-desc">{task.description}</div>}
            {task.last_error && (
              <div style={{
                marginTop: 6,
                padding: '6px 10px',
                background: 'rgba(255,80,80,0.1)',
                border: '1px solid rgba(255,80,80,0.3)',
                borderRadius: 4,
                fontSize: 12,
                color: 'var(--error, #ff6b6b)',
                fontFamily: 'monospace',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}>
                ⚠ {task.last_error}
              </div>
            )}
            {actions.length > 0 && (
              <div className="task-detail-actions">
                {actions.map(([status, label]) => (
                  <button
                    key={status}
                    onClick={() => onStatusChange(task.id, status)}
                    style={{ fontSize: 12, padding: '4px 10px' }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
