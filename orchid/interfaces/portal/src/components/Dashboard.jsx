import { useState, useEffect, useCallback, useRef } from 'react'
import { StatusBadge, TypeBadge } from './StatusBadge.jsx'
import TaskRunHistory from './TaskRunHistory.jsx'
import TaskFormModal from './TaskFormModal.jsx'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtRelative(iso) {
  if (!iso) return null
  const delta = Date.now() - new Date(iso).getTime()
  const abs = Math.abs(delta)
  const future = delta < 0
  if (abs < 60000) return 'just now'
  const mins  = Math.floor(abs / 60000)
  const hours = Math.floor(abs / 3600000)
  const days  = Math.floor(abs / 86400000)
  const label = mins < 60 ? `${mins}m` : hours < 24 ? `${hours}h` : `${days}d`
  return future ? `in ${label}` : `${label} ago`
}

// ── Delete confirm ────────────────────────────────────────────────────────────

function DeleteConfirm({ task, onConfirm, onCancel }) {
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onCancel])

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onCancel()}>
      <div className="modal" style={{ maxWidth: 380 }}>
        <div className="modal-header">
          <span className="modal-title">🗑 Delete task?</span>
        </div>
        <div className="modal-body">
          <p style={{ fontSize: 14, lineHeight: 1.6 }}>
            Delete <strong>{task.name}</strong>? This removes the task and all scheduled runs.
            Cannot be undone.
          </p>
        </div>
        <div className="modal-footer">
          <button onClick={onCancel}>Cancel</button>
          <button className="danger" onClick={onConfirm}>Delete</button>
        </div>
      </div>
    </div>
  )
}

// ── Task row ──────────────────────────────────────────────────────────────────

function TaskRow({ task, onRunNow, onHistory, onDuplicate, onEdit, onDelete }) {
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState(null)

  const handleRun = async () => {
    setRunning(true)
    setRunError(null)
    try {
      await onRunNow(task.task_id)
    } catch (e) {
      setRunError(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="card" style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '12px 16px' }}>
      {/* Status dot */}
      <div style={{ paddingTop: 3, flexShrink: 0 }}>
        {task.last_run_status === 'running' && <span className="dot-running" />}
        {task.last_run_status === 'success' && <span className="dot-success" />}
        {(task.last_run_status === 'failure' || task.last_run_status === 'timeout') && <span className="dot-error" />}
        {!task.last_run_status && (
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--border)', display: 'inline-block' }} />
        )}
      </div>

      {/* Info */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, fontSize: 14, color: task.enabled ? 'var(--text)' : 'var(--text-dim)' }}>
            {task.name}
          </span>
          <TypeBadge type={task.task_type} />
          {!task.enabled && <span className="badge badge-idle">disabled</span>}
        </div>

        {task.description && (
          <div style={{
            fontSize: 12, color: 'var(--text-dim)', marginTop: 2,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {task.description}
          </div>
        )}

        <div style={{ display: 'flex', gap: 14, marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>
            {task.schedule}
          </span>
          {task.last_run_at && (
            <span style={{ fontSize: 11, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 5 }}>
              Last: {fmtRelative(task.last_run_at)} <StatusBadge status={task.last_run_status} />
            </span>
          )}
          {task.next_run_at && (
            <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
              Next: {fmtRelative(task.next_run_at)}
            </span>
          )}
        </div>

        {runError && (
          <div style={{ fontSize: 11, color: 'var(--error-fg)', marginTop: 4 }}>⚠ {runError}</div>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 5, flexShrink: 0, alignItems: 'center' }}>
        <button
          className="ghost icon"
          onClick={handleRun}
          disabled={running}
          title="Run now"
        >
          {running
            ? <span className="spinner" style={{ width: 13, height: 13, borderWidth: 2 }} />
            : '▶'}
        </button>
        <button className="ghost icon" onClick={() => onHistory(task)} title="Run history">📋</button>
        <button className="ghost icon" onClick={() => onDuplicate(task)} title="Duplicate">⧉</button>
        <button className="ghost icon" onClick={() => onEdit(task)} title="Edit">✏️</button>
        <button
          className="ghost icon"
          onClick={() => onDelete(task)}
          title="Delete"
          style={{ color: 'var(--error-fg)' }}
        >
          🗑
        </button>
      </div>
    </div>
  )
}

// ── Project card ──────────────────────────────────────────────────────────────

function ProjectCard({ project }) {
  const todo   = project.todo            ?? 0
  const done   = project.done            ?? 0
  const inprog = project.in_progress     ?? 0
  const blocked = project.blocked        ?? 0
  const total  = todo + done + inprog + blocked
  const pct    = total > 0 ? Math.round((done / total) * 100) : null
  const href   = `/projects/?p=${encodeURIComponent(project.id)}`

  return (
    <a href={href} style={{ textDecoration: 'none', color: 'inherit' }}>
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10, cursor: 'pointer' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {project.name}
          </div>
          {project.path && (
            <div style={{
              fontSize: 11, color: 'var(--text-mute)', marginTop: 2,
              fontFamily: 'var(--mono)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {project.path.replace(/^\/home\/[^/]+/, '~').replace(/^\/Users\/[^/]+/, '~')}
            </div>
          )}
        </div>
        {project.running && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--success-fg)', flexShrink: 0 }}>
            <span className="dot-running" />running
          </span>
        )}
      </div>

      {total > 0 ? (
        <div>
          <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--text-dim)', marginBottom: 6, flexWrap: 'wrap' }}>
            {inprog > 0  && <span style={{ color: 'var(--accent-2)' }}>● {inprog} active</span>}
            {todo > 0    && <span>○ {todo} todo</span>}
            {blocked > 0 && <span style={{ color: 'var(--warning-fg)' }}>⚠ {blocked} blocked</span>}
            {done > 0    && <span style={{ color: 'var(--success-fg)' }}>✓ {done} done</span>}
          </div>
          <div style={{ height: 4, background: 'var(--surface2)', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{
              height: '100%',
              width: `${pct ?? 0}%`,
              background: pct === 100 ? 'var(--success-fg)' : 'var(--accent)',
              borderRadius: 2,
              transition: 'width 0.3s',
            }} />
          </div>
        </div>
      ) : (
        <span style={{ fontSize: 12, color: 'var(--text-mute)' }}>No tasks</span>
      )}
    </div>
    </a>
  )
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function Toast({ msg }) {
  return (
    <div style={{
      position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)',
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 8, padding: '10px 20px',
      fontSize: 13, color: 'var(--text)',
      boxShadow: 'var(--shadow)',
      zIndex: 600, whiteSpace: 'nowrap',
      pointerEvents: 'none',
    }}>
      {msg}
    </div>
  )
}

// ── Collapsible Section ───────────────────────────────────────────────────────

function CollapsibleSection({ title, count, loading, headerAction, children }) {
  const [open, setOpen] = useState(true)
  const autoDone = useRef(false)

  useEffect(() => {
    if (!loading && !autoDone.current) {
      autoDone.current = true
      if (count > 2) setOpen(false)
    }
  }, [loading, count])

  return (
    <section style={{ marginBottom: 40 }}>
      <div className="section-header">
        <button
          onClick={() => setOpen(o => !o)}
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            background: 'none', border: 'none', padding: 0,
            cursor: 'pointer', font: 'inherit', color: 'inherit',
          }}
        >
          <span className="section-title">{title}</span>
          {!open && count > 0 && (
            <span style={{
              fontSize: 11, fontWeight: 600,
              background: 'var(--surface2)', color: 'var(--text-dim)',
              borderRadius: 10, padding: '1px 7px',
            }}>{count}</span>
          )}
          <span style={{ fontSize: 14, color: 'var(--text-dim)', marginLeft: 4, lineHeight: 1 }}>
            {open ? '▾' : '▸'}
          </span>
        </button>
        {headerAction}
      </div>
      {open && children}
    </section>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard({ tasks, tasksLoading, projects, projectsLoading, taskOps }) {
  const [historyTask,    setHistoryTask]    = useState(null)
  const [editTask,       setEditTask]       = useState(null)
  const [duplicateTask,  setDuplicateTask]  = useState(null)
  const [deleteTask,     setDeleteTask]     = useState(null)
  const [showCreate,     setShowCreate]     = useState(false)
  const [toastMsg,       setToastMsg]       = useState(null)

  const toast = (msg) => {
    setToastMsg(msg)
    setTimeout(() => setToastMsg(null), 3000)
  }

  const handleRunNow = useCallback(async (taskId) => {
    await taskOps.runNow(taskId)
    toast('Task dispatched ▶')
    setTimeout(taskOps.refresh, 800)
  }, [taskOps])

  const handleDelete = async () => {
    try {
      await taskOps.deleteTask(deleteTask.task_id)
      setDeleteTask(null)
      toast('Task deleted')
    } catch (e) {
      toast(`Delete failed: ${e.message}`)
      setDeleteTask(null)
    }
  }

  const activeTasks    = tasks.filter(t => t.enabled)
  const disabledTasks  = tasks.filter(t => !t.enabled)
  const activeProjects = projects.filter(p => p.active !== false)

  return (
    <div className="page">
      {/* ── Scheduled Tasks ─────────────────────────────────────────────── */}
      <CollapsibleSection
        title="⏰ Scheduled Tasks"
        count={tasks.length}
        loading={tasksLoading}
        headerAction={
          <button
            className="primary"
            style={{ fontSize: 12, padding: '5px 12px' }}
            onClick={() => setShowCreate(true)}
          >
            + New task
          </button>
        }
      >
        {tasksLoading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-dim)', padding: '20px 0' }}>
            <span className="spinner" /> Loading…
          </div>
        ) : tasks.length === 0 ? (
          <div className="empty-state" style={{
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)',
          }}>
            <span className="empty-icon">⏰</span>
            <span className="empty-text">No scheduled tasks yet</span>
            <span className="empty-sub">Automate agent prompts, MCP calls, or shell commands on a cron schedule.</span>
            <button className="primary" style={{ marginTop: 8 }} onClick={() => setShowCreate(true)}>
              + New task
            </button>
          </div>
        ) : (
          <div className="task-list">
            {activeTasks.map(task => (
              <TaskRow
                key={task.task_id}
                task={task}
                onRunNow={handleRunNow}
                onHistory={setHistoryTask}
                onDuplicate={setDuplicateTask}
                onEdit={setEditTask}
                onDelete={setDeleteTask}
              />
            ))}
            {disabledTasks.length > 0 && activeTasks.length > 0 && (
              <div style={{ fontSize: 11, color: 'var(--text-mute)', margin: '8px 4px 2px', letterSpacing: '0.4px' }}>
                DISABLED
              </div>
            )}
            {disabledTasks.map(task => (
              <TaskRow
                key={task.task_id}
                task={task}
                onRunNow={handleRunNow}
                onHistory={setHistoryTask}
                onDuplicate={setDuplicateTask}
                onEdit={setEditTask}
                onDelete={setDeleteTask}
              />
            ))}
          </div>
        )}
      </CollapsibleSection>

      {/* ── Projects ────────────────────────────────────────────────────── */}
      <CollapsibleSection
        title="📁 Projects"
        count={activeProjects.length}
        loading={projectsLoading}
      >
        {projectsLoading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-dim)', padding: '20px 0' }}>
            <span className="spinner" /> Loading…
          </div>
        ) : activeProjects.length === 0 ? (
          <div className="empty-state" style={{
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)',
          }}>
            <span className="empty-icon">📁</span>
            <span className="empty-text">No active projects</span>
            <span className="empty-sub">An admin can assign projects to your account.</span>
          </div>
        ) : (
          <div className="grid-2">
            {activeProjects.map(p => <ProjectCard key={p.id} project={p} />)}
          </div>
        )}
      </CollapsibleSection>

      {/* ── Modals ──────────────────────────────────────────────────────── */}
      {historyTask && (
        <TaskRunHistory
          task={historyTask}
          getRuns={taskOps.getRuns}
          onClose={() => setHistoryTask(null)}
        />
      )}

      {editTask && (
        <TaskFormModal
          initial={editTask}
          onSave={(body) => taskOps.updateTask(editTask.task_id, body)}
          onClose={() => setEditTask(null)}
        />
      )}

      {duplicateTask && (
        <TaskFormModal
          initial={{ ...duplicateTask, name: duplicateTask.name + ' copy' }}
          isDuplicate
          onSave={taskOps.createTask}
          onClose={() => setDuplicateTask(null)}
        />
      )}

      {showCreate && (
        <TaskFormModal
          onSave={taskOps.createTask}
          onClose={() => setShowCreate(false)}
        />
      )}

      {deleteTask && (
        <DeleteConfirm
          task={deleteTask}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTask(null)}
        />
      )}

      {toastMsg && <Toast msg={toastMsg} />}
    </div>
  )
}
