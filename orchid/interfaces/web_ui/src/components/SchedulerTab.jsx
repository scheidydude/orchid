import { useState, useEffect, useCallback, useMemo } from 'react'
import CronBuilder from './CronBuilder.jsx'

// ── helpers ──────────────────────────────────────────────────────────────────

const STATUS_COLOR = {
  success: 'var(--success)',
  failure: 'var(--error)',
  timeout: 'var(--warning)',
  running: 'var(--accent)',
}

const TYPE_LABELS = {
  agent_prompt: 'Agent',
  mcp_tool: 'MCP',
  shell: 'Shell',
}

const CONFIG_TEMPLATES = {
  agent_prompt: JSON.stringify({ prompt: 'Summarise today\'s activity', project: '' }, null, 2),
  mcp_tool: JSON.stringify({ server: '', tool: '', args: {} }, null, 2),
  shell: JSON.stringify({ command: 'echo hello' }, null, 2),
}

// Build a minimal args object from a JSON Schema parameters dict
function buildArgsTemplate(parameters) {
  const props = parameters?.properties || {}
  const args = {}
  for (const [key, schema] of Object.entries(props)) {
    if (schema.type === 'string')           args[key] = ''
    else if (schema.type === 'integer')     args[key] = 0
    else if (schema.type === 'number')      args[key] = 0
    else if (schema.type === 'boolean')     args[key] = false
    else if (schema.type === 'array')       args[key] = []
    else if (schema.type === 'object')      args[key] = {}
    else                                    args[key] = null
  }
  return args
}

const SCHEDULE_PRESETS = [
  { label: 'Every minute', value: '* * * * *' },
  { label: 'Every 15 min', value: '*/15 * * * *' },
  { label: 'Every hour', value: '0 * * * *' },
  { label: 'Daily 9 am', value: '0 9 * * *' },
  { label: 'Daily midnight', value: '0 0 * * *' },
  { label: 'Weekly Mon 9 am', value: '0 9 * * 1' },
]

function fmtDatetime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch { return iso }
}

function fmtDuration(start, end) {
  if (!start || !end) return '—'
  const ms = new Date(end) - new Date(start)
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}

// ── StatusBadge ───────────────────────────────────────────────────────────────

function StatusBadge({ status }) {
  if (!status) return <span style={{ color: 'var(--text-dim)', fontSize: 11 }}>never</span>
  return (
    <span style={{
      fontSize: 11,
      fontWeight: 600,
      color: STATUS_COLOR[status] || 'var(--text-dim)',
      background: `${STATUS_COLOR[status] || 'var(--border)'}22`,
      borderRadius: 4,
      padding: '1px 6px',
      textTransform: 'uppercase',
      letterSpacing: '0.3px',
    }}>
      {status}
    </span>
  )
}

// ── TypeBadge ─────────────────────────────────────────────────────────────────

function TypeBadge({ type }) {
  return (
    <span style={{
      fontSize: 11,
      color: 'var(--accent-2)',
      background: 'var(--accent)22',
      borderRadius: 4,
      padding: '1px 6px',
      fontWeight: 600,
    }}>
      {TYPE_LABELS[type] || type}
    </span>
  )
}

// ── McpToolPicker ─────────────────────────────────────────────────────────────

function McpToolPicker({ selectedKey, onSelect }) {
  const [servers, setServers]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [collapsed, setCollapsed] = useState({})   // server → bool

  useEffect(() => {
    setLoading(true)
    fetch('/api/scheduler/mcp-tools')
      .then(r => r.json())
      .then(d => {
        setServers(d.servers || [])
        setError(null)
        // Auto-expand all servers (tools or errors both want to be visible)
        const init = {}
        for (const s of (d.servers || [])) {
          init[s.server] = false  // false = expanded
        }
        setCollapsed(init)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const toggle = (name) => setCollapsed(c => ({ ...c, [name]: !c[name] }))

  const totalTools = useMemo(() => servers.reduce((n, s) => n + (s.tools?.length || 0), 0), [servers])

  if (loading) return <div className="loading" style={{ padding: '6px 0', fontSize: 12 }}>Fetching MCP tools…</div>

  if (error) return (
    <div style={{ color: 'var(--error)', fontSize: 12, padding: '4px 0' }}>
      Failed to load MCP tools: {error}
    </div>
  )

  if (!servers.length) return (
    <div style={{
      fontSize: 12, color: 'var(--text-dim)',
      padding: '8px 10px',
      background: 'var(--bg)',
      borderRadius: 'var(--radius)',
      border: '1px solid var(--border)',
    }}>
      No MCP servers configured. Add servers to <code>mcp_servers:</code> in{' '}
      <code>~/.config/orchid/config.yaml</code> then restart the server.
    </div>
  )

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
      {servers.map((srv, si) => (
        <div key={srv.server} style={{ borderTop: si > 0 ? '1px solid var(--border)' : undefined }}>
          {/* Server header */}
          <div
            onClick={() => toggle(srv.server)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 12px',
              background: 'var(--surface2)',
              cursor: 'pointer',
              userSelect: 'none',
            }}
          >
            <span style={{ fontSize: 11, color: 'var(--text-dim)', transform: collapsed[srv.server] ? 'rotate(-90deg)' : 'rotate(0)', display: 'inline-block', transition: 'transform 0.15s' }}>▼</span>
            <span style={{ fontWeight: 600, fontSize: 12 }}>{srv.server}</span>
            <span style={{ fontSize: 11, color: 'var(--text-dim)', marginLeft: 'auto' }}>
              {srv.error
                ? <span style={{ color: 'var(--error)' }}>⚠ {srv.error.slice(0, 50)}</span>
                : `${srv.tools.length} tool${srv.tools.length !== 1 ? 's' : ''}`
              }
            </span>
          </div>

          {/* Tool list */}
          {!collapsed[srv.server] && !srv.error && srv.tools.map(tool => {
            const key = `${srv.server}::${tool.name}`
            const active = selectedKey === key
            return (
              <div
                key={tool.name}
                onClick={() => onSelect(srv.server, tool.name, tool.parameters)}
                style={{
                  display: 'flex', alignItems: 'flex-start', gap: 10,
                  padding: '8px 14px',
                  cursor: 'pointer',
                  background: active ? 'var(--accent)18' : 'transparent',
                  borderLeft: active ? '3px solid var(--accent)' : '3px solid transparent',
                  transition: 'background 0.1s',
                }}
                onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'var(--surface2)' }}
                onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
              >
                <span style={{ fontSize: 14, marginTop: 1, flexShrink: 0 }}>
                  {active ? '✅' : '○'}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 13, fontFamily: 'var(--mono)' }}>
                    {tool.name}
                  </div>
                  {tool.description && (
                    <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2 }}>
                      {tool.description}
                    </div>
                  )}
                  {tool.parameters?.properties && Object.keys(tool.parameters.properties).length > 0 && (
                    <div style={{ fontSize: 11, color: 'var(--accent-2)', marginTop: 4 }}>
                      params: {Object.keys(tool.parameters.properties).join(', ')}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

// ── RunHistory ────────────────────────────────────────────────────────────────

function RunHistory({ taskId }) {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    fetch(`/api/scheduler/tasks/${taskId}/runs?limit=20`)
      .then(r => r.json())
      .then(d => setRuns(d.runs || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [taskId])

  if (loading) return <div className="loading" style={{ padding: '8px 0' }}>Loading runs…</div>
  if (!runs.length) return <div style={{ color: 'var(--text-dim)', fontSize: 12, padding: '8px 0' }}>No runs yet.</div>

  return (
    <div style={{ marginTop: 4 }}>
      {runs.map(run => (
        <div key={run.run_id} style={{
          borderTop: '1px solid var(--border)',
          padding: '8px 0',
        }}>
          <div
            style={{ display: 'flex', gap: 12, alignItems: 'center', cursor: 'pointer' }}
            onClick={() => setExpanded(e => e === run.run_id ? null : run.run_id)}
          >
            <StatusBadge status={run.status} />
            <span style={{ fontSize: 12, color: 'var(--text-dim)', minWidth: 120 }}>
              {fmtDatetime(run.started_at)}
            </span>
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
              {fmtDuration(run.started_at, run.finished_at)}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-dim)', marginLeft: 'auto' }}>
              {expanded === run.run_id ? '▲' : '▼'}
            </span>
          </div>
          {expanded === run.run_id && (
            <div style={{
              marginTop: 6,
              padding: '8px 10px',
              background: 'var(--bg)',
              borderRadius: 'var(--radius)',
              fontSize: 12,
              fontFamily: 'var(--mono)',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
              maxHeight: 200,
              overflowY: 'auto',
              color: run.error ? 'var(--error)' : 'var(--text)',
            }}>
              {run.error || run.output || '(no output)'}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ── TaskModal (create / edit) ─────────────────────────────────────────────────

function TaskModal({ task, onSave, onClose }) {
  const isEdit = !!task
  const [form, setForm] = useState({
    name: task?.name || '',
    description: task?.description || '',
    enabled: task?.enabled ?? true,
    schedule: task?.schedule || '0 9 * * *',
    task_type: task?.task_type || 'agent_prompt',
    config: task?.config ? JSON.stringify(task.config, null, 2) : CONFIG_TEMPLATES.agent_prompt,
    notify_on_failure: task?.notify_on_failure ?? true,
    notify_on_success: task?.notify_on_success ?? false,
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [showCronBuilder, setShowCronBuilder] = useState(false)

  // Track selected MCP tool as "server::toolName" for the picker
  const initMcpKey = () => {
    if ((task?.task_type || 'agent_prompt') !== 'mcp_tool') return null
    try {
      const cfg = typeof task?.config === 'object' ? task.config : JSON.parse(task?.config || '{}')
      return cfg.server && cfg.tool ? `${cfg.server}::${cfg.tool}` : null
    } catch { return null }
  }
  const [mcpToolKey, setMcpToolKey] = useState(initMcpKey)

  const setField = (key, val) => setForm(f => ({ ...f, [key]: val }))

  const handleTypeChange = (type) => {
    if (type !== 'mcp_tool') setMcpToolKey(null)
    setForm(f => ({
      ...f,
      task_type: type,
      config: CONFIG_TEMPLATES[type] || '{}',
    }))
  }

  const handleMcpToolSelect = (server, toolName, parameters) => {
    const key = `${server}::${toolName}`
    setMcpToolKey(key)
    const args = buildArgsTemplate(parameters)
    setField('config', JSON.stringify({ server, tool: toolName, args }, null, 2))
  }

  const handlePreset = (val) => setField('schedule', val)

  const submit = async () => {
    setError(null)
    let configObj
    try {
      configObj = JSON.parse(form.config)
    } catch {
      setError('Config must be valid JSON')
      return
    }
    const body = {
      name: form.name.trim(),
      description: form.description.trim(),
      enabled: form.enabled,
      schedule: form.schedule.trim(),
      task_type: form.task_type,
      config: configObj,
      notify_on_failure: form.notify_on_failure,
      notify_on_success: form.notify_on_success,
    }
    setSaving(true)
    try {
      const url = isEdit ? `/api/scheduler/tasks/${task.task_id}` : '/api/scheduler/tasks'
      const res = await fetch(url, {
        method: isEdit ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail?.errors?.join(', ') || `HTTP ${res.status}`)
      }
      const saved = await res.json()
      onSave(saved)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: '#000a',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000,
    }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 24,
        width: '100%',
        maxWidth: 540,
        maxHeight: '90vh',
        overflowY: 'auto',
      }}>
        <h3 style={{ marginBottom: 20 }}>{isEdit ? 'Edit Task' : 'New Scheduled Task'}</h3>

        <div className="settings-form">
          {/* Name */}
          <div className="form-group">
            <label>Name *</label>
            <input
              value={form.name}
              onChange={e => setField('name', e.target.value)}
              placeholder="Daily digest"
              autoFocus
            />
          </div>

          {/* Description */}
          <div className="form-group">
            <label>Description</label>
            <input
              value={form.description}
              onChange={e => setField('description', e.target.value)}
              placeholder="Optional description"
            />
          </div>

          {/* Schedule */}
          <div className="form-group">
            <label>Schedule (cron) *</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                value={form.schedule}
                onChange={e => setField('schedule', e.target.value)}
                placeholder="0 9 * * *"
                style={{ flex: 1, fontFamily: 'var(--mono)' }}
              />
              <select
                value=""
                onChange={e => e.target.value && handlePreset(e.target.value)}
                style={{ width: 'auto', flexShrink: 0, fontSize: 12 }}
              >
                <option value="">Presets…</option>
                {SCHEDULE_PRESETS.map(p => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
              <button
                type="button"
                title="Open schedule builder"
                onClick={() => setShowCronBuilder(true)}
                style={{ flexShrink: 0, fontSize: 13, padding: '4px 10px' }}
              >
                🗓
              </button>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 3 }}>
              min hour dom month dow &nbsp;·&nbsp; stored as UTC
            </div>
          </div>

          {/* Task type */}
          <div className="form-group">
            <label>Task type *</label>
            <select
              value={form.task_type}
              onChange={e => handleTypeChange(e.target.value)}
            >
              <option value="agent_prompt">agent_prompt — run an AI agent</option>
              <option value="mcp_tool">mcp_tool — call an MCP tool</option>
              <option value="shell">shell — run a shell command</option>
            </select>
          </div>

          {/* Config — mcp_tool gets a tool picker; others get raw JSON */}
          {form.task_type === 'mcp_tool' ? (
            <>
              <div className="form-group">
                <label>Select tool *</label>
                <McpToolPicker
                  selectedKey={mcpToolKey}
                  onSelect={handleMcpToolSelect}
                />
              </div>
              <div className="form-group">
                <label>
                  Config (JSON) *
                  <span style={{ fontWeight: 400, color: 'var(--text-dim)', marginLeft: 8, fontSize: 11 }}>
                    server &amp; tool set by picker · edit <code>args</code> as needed
                  </span>
                </label>
                <textarea
                  value={form.config}
                  onChange={e => setField('config', e.target.value)}
                  rows={6}
                  style={{ fontFamily: 'var(--mono)', fontSize: 12, resize: 'vertical' }}
                />
              </div>
            </>
          ) : (
            <div className="form-group">
              <label>Config (JSON) *</label>
              <textarea
                value={form.config}
                onChange={e => setField('config', e.target.value)}
                rows={6}
                style={{ fontFamily: 'var(--mono)', fontSize: 12, resize: 'vertical' }}
              />
            </div>
          )}

          {/* Enabled */}
          <div className="form-group" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox"
              id="sched-enabled"
              checked={form.enabled}
              onChange={e => setField('enabled', e.target.checked)}
              style={{ width: 'auto' }}
            />
            <label htmlFor="sched-enabled" style={{ margin: 0 }}>Enabled</label>
          </div>

          {/* Notifications */}
          <div style={{ display: 'flex', gap: 20 }}>
            <div className="form-group" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                id="sched-notify-fail"
                checked={form.notify_on_failure}
                onChange={e => setField('notify_on_failure', e.target.checked)}
                style={{ width: 'auto' }}
              />
              <label htmlFor="sched-notify-fail" style={{ margin: 0 }}>Notify on failure</label>
            </div>
            <div className="form-group" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                id="sched-notify-ok"
                checked={form.notify_on_success}
                onChange={e => setField('notify_on_success', e.target.checked)}
                style={{ width: 'auto' }}
              />
              <label htmlFor="sched-notify-ok" style={{ margin: 0 }}>Notify on success</label>
            </div>
          </div>

          {error && <div className="error-msg" style={{ color: 'var(--error)', fontSize: 12, marginTop: 4 }}>{error}</div>}

          <div className="settings-form-actions" style={{ marginTop: 16 }}>
            <button className="primary" onClick={submit} disabled={saving}>
              {saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Create Task'}
            </button>
            <button onClick={onClose}>Cancel</button>
          </div>
        </div>
      </div>

      {showCronBuilder && (
        <CronBuilder
          onApply={(cron) => {
            setField('schedule', cron)
            setShowCronBuilder(false)
          }}
          onClose={() => setShowCronBuilder(false)}
        />
      )}
    </div>
  )
}

// ── SchedulerTab (main) ───────────────────────────────────────────────────────

export default function SchedulerTab() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [modal, setModal] = useState(null)   // null | 'create' | {task}
  const [expanded, setExpanded] = useState(null) // task_id with history open
  const [runningNow, setRunningNow] = useState(new Set())

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch('/api/scheduler/tasks')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const d = await res.json()
      setTasks(d.tasks || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchTasks() }, [fetchTasks])

  // Poll to pick up last_run_status changes
  useEffect(() => {
    const id = setInterval(fetchTasks, 15000)
    return () => clearInterval(id)
  }, [fetchTasks])

  const handleSave = (saved) => {
    setTasks(prev => {
      const idx = prev.findIndex(t => t.task_id === saved.task_id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = saved
        return next
      }
      return [...prev, saved]
    })
    setModal(null)
  }

  const handleDelete = async (task) => {
    if (!confirm(`Delete "${task.name}"?`)) return
    try {
      const res = await fetch(`/api/scheduler/tasks/${task.task_id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setTasks(prev => prev.filter(t => t.task_id !== task.task_id))
      if (expanded === task.task_id) setExpanded(null)
    } catch (e) {
      alert(`Delete failed: ${e.message}`)
    }
  }

  const handleToggleEnabled = async (task) => {
    try {
      const body = { ...task, enabled: !task.enabled }
      const res = await fetch(`/api/scheduler/tasks/${task.task_id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const updated = await res.json()
      setTasks(prev => prev.map(t => t.task_id === task.task_id ? updated : t))
    } catch (e) {
      alert(`Toggle failed: ${e.message}`)
    }
  }

  const handleRunNow = async (task) => {
    setRunningNow(s => new Set([...s, task.task_id]))
    try {
      const res = await fetch(`/api/scheduler/tasks/${task.task_id}/run`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      // Expand history after trigger so user can watch
      setExpanded(task.task_id)
      // Refresh task list after a short delay to pick up status update
      setTimeout(fetchTasks, 2000)
    } catch (e) {
      alert(`Run failed: ${e.message}`)
    } finally {
      setRunningNow(s => { const n = new Set(s); n.delete(task.task_id); return n })
    }
  }

  const toggleHistory = (taskId) => {
    setExpanded(e => e === taskId ? null : taskId)
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '0 4px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <h3 style={{ flex: 1 }}>Scheduled Tasks</h3>
        <button className="primary" onClick={() => setModal('create')}>+ New Task</button>
      </div>

      {/* Error */}
      {error && (
        <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>
          Failed to load tasks: {error}
        </div>
      )}

      {/* Loading */}
      {loading && <div className="loading">Loading…</div>}

      {/* Empty state */}
      {!loading && !error && tasks.length === 0 && (
        <div style={{
          textAlign: 'center',
          color: 'var(--text-dim)',
          padding: '48px 0',
          fontSize: 14,
        }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>⏰</div>
          No scheduled tasks yet.
          <br />
          <button
            className="primary"
            style={{ marginTop: 16 }}
            onClick={() => setModal('create')}
          >
            Create your first task
          </button>
        </div>
      )}

      {/* Task list */}
      {tasks.map(task => (
        <div key={task.task_id} style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          marginBottom: 10,
          overflow: 'hidden',
        }}>
          {/* Task row */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '12px 14px',
            flexWrap: 'wrap',
          }}>
            {/* Enabled toggle */}
            <input
              type="checkbox"
              checked={task.enabled}
              onChange={() => handleToggleEnabled(task)}
              title={task.enabled ? 'Disable' : 'Enable'}
              style={{ width: 'auto', flexShrink: 0 }}
            />

            {/* Name + description */}
            <div style={{ flex: 1, minWidth: 120 }}>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{task.name}</div>
              {task.description && (
                <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2 }}>
                  {task.description}
                </div>
              )}
            </div>

            {/* Type badge */}
            <TypeBadge type={task.task_type} />

            {/* Schedule */}
            <code style={{
              fontSize: 11,
              color: 'var(--text-dim)',
              background: 'var(--bg)',
              borderRadius: 4,
              padding: '2px 6px',
            }}>
              {task.schedule}
            </code>

            {/* Last run status */}
            <div style={{ textAlign: 'right', minWidth: 80 }}>
              <StatusBadge status={task.last_run_status} />
              {task.last_run_at && (
                <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 2 }}>
                  {fmtDatetime(task.last_run_at)}
                </div>
              )}
            </div>

            {/* Next run */}
            {task.next_run_at && (
              <div style={{ fontSize: 11, color: 'var(--text-dim)', minWidth: 90 }}>
                next {fmtDatetime(task.next_run_at)}
              </div>
            )}

            {/* Actions */}
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <button
                onClick={() => handleRunNow(task)}
                disabled={runningNow.has(task.task_id)}
                title="Run now"
                style={{ fontSize: 12, padding: '4px 10px' }}
              >
                {runningNow.has(task.task_id) ? '…' : '▶'}
              </button>
              <button
                onClick={() => setModal(task)}
                title="Edit"
                style={{ fontSize: 12, padding: '4px 10px' }}
              >
                ✏
              </button>
              <button
                onClick={() => handleDelete(task)}
                className="danger"
                title="Delete"
                style={{ fontSize: 12, padding: '4px 10px' }}
              >
                ✕
              </button>
              <button
                onClick={() => toggleHistory(task.task_id)}
                title="Run history"
                style={{
                  fontSize: 12,
                  padding: '4px 10px',
                  borderColor: expanded === task.task_id ? 'var(--accent)' : undefined,
                  color: expanded === task.task_id ? 'var(--accent-2)' : undefined,
                }}
              >
                {expanded === task.task_id ? 'History ▲' : 'History ▼'}
              </button>
            </div>
          </div>

          {/* Run history (expandable) */}
          {expanded === task.task_id && (
            <div style={{
              borderTop: '1px solid var(--border)',
              padding: '12px 14px',
              background: 'var(--surface2)',
            }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 4 }}>
                Run History
              </div>
              <RunHistory taskId={task.task_id} />
            </div>
          )}
        </div>
      ))}

      {/* Modal */}
      {modal && (
        <TaskModal
          task={modal === 'create' ? null : modal}
          onSave={handleSave}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  )
}
