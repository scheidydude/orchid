import { useState, useEffect, useRef } from 'react'

const SCHEDULE_PRESETS = [
  { label: 'Every minute',  value: '* * * * *' },
  { label: 'Every 15 min', value: '*/15 * * * *' },
  { label: 'Every hour',   value: '0 * * * *' },
  { label: 'Daily 9 am',   value: '0 9 * * *' },
  { label: 'Daily midnight', value: '0 0 * * *' },
  { label: 'Weekly Mon 9 am', value: '0 9 * * 1' },
]

const BLANK_CONFIGS = {
  agent_prompt: { prompt: '', system: '' },
  agent_tool:   { servers: [], prompt: '', system: '' },
  mcp_tool:     { server: '', tool: '', args: {} },
  shell:        { command: '' },
}

export default function TaskFormModal({ initial, onSave, onClose, isDuplicate = false }) {
  const editing = !!initial && !isDuplicate

  const [name, setName]           = useState(initial?.name || '')
  const [desc, setDesc]           = useState(initial?.description || '')
  const [schedule, setSchedule]   = useState(initial?.schedule || '0 9 * * *')
  const [taskType, setTaskType]   = useState(initial?.task_type || 'agent_prompt')
  const [configStr, setConfigStr] = useState(
    initial?.config ? JSON.stringify(initial.config, null, 2) : JSON.stringify(BLANK_CONFIGS['agent_prompt'], null, 2)
  )
  const [enabled, setEnabled]     = useState(initial?.enabled ?? true)
  const [notifyFail, setNotifyFail] = useState(initial?.notify_on_failure ?? true)
  const [notifyOk, setNotifyOk]   = useState(initial?.notify_on_success ?? false)
  const [error, setError]         = useState('')
  const [saving, setSaving]       = useState(false)

  // Reset config template only when user actively changes the task type select
  const prevTaskType = useRef(taskType)
  useEffect(() => {
    if (!editing && taskType !== prevTaskType.current) {
      setConfigStr(JSON.stringify(BLANK_CONFIGS[taskType] || {}, null, 2))
    }
    prevTaskType.current = taskType
  }, [taskType, editing])

  // Close on Escape
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose])

  const handleSave = async () => {
    setError('')
    let config
    try { config = JSON.parse(configStr) }
    catch { setError('Config is not valid JSON'); return }
    setSaving(true)
    try {
      await onSave({
        name: name.trim(),
        description: desc.trim(),
        schedule,
        task_type: taskType,
        config,
        enabled,
        notify_on_failure: notifyFail,
        notify_on_success: notifyOk,
      })
      onClose()
    } catch (e) {
      setError(e.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <span className="modal-title">{isDuplicate ? '⧉ Duplicate Task' : editing ? '✏️ Edit Task' : '＋ New Scheduled Task'}</span>
          <button className="ghost icon" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Name */}
          <div className="field">
            <label>Name *</label>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Daily standup report" autoFocus />
          </div>

          {/* Description */}
          <div className="field">
            <label>Description</label>
            <input value={desc} onChange={e => setDesc(e.target.value)} placeholder="Optional note" />
          </div>

          {/* Task type */}
          <div className="field">
            <label>Task type *</label>
            <select value={taskType} onChange={e => setTaskType(e.target.value)}>
              <option value="agent_prompt">Agent prompt</option>
              <option value="agent_tool">Agent tool (MCP)</option>
              <option value="mcp_tool">MCP tool (single call)</option>
              <option value="shell">Shell command</option>
            </select>
          </div>

          {/* Schedule */}
          <div className="field">
            <label>Schedule (cron) *</label>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
              {SCHEDULE_PRESETS.map(p => (
                <button
                  key={p.value}
                  type="button"
                  style={{
                    padding: '3px 10px', fontSize: 11,
                    borderRadius: 4,
                    background: schedule === p.value ? 'var(--accent)' : 'var(--surface2)',
                    borderColor: schedule === p.value ? 'var(--accent)' : 'var(--border)',
                    color: schedule === p.value ? '#fff' : 'var(--text-dim)',
                  }}
                  onClick={() => setSchedule(p.value)}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <input
              value={schedule}
              onChange={e => setSchedule(e.target.value)}
              placeholder="* * * * *"
              style={{ fontFamily: 'var(--mono)', fontSize: 13 }}
            />
            <span className="hint">min hour dom month dow — all UTC</span>
          </div>

          {/* Config */}
          <div className="field">
            <label>Config (JSON) *</label>
            <textarea
              value={configStr}
              onChange={e => setConfigStr(e.target.value)}
              rows={6}
              style={{ fontFamily: 'var(--mono)', fontSize: 12, resize: 'vertical' }}
            />
          </div>

          {/* Toggles */}
          <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
            {[
              [enabled, setEnabled, 'Enabled'],
              [notifyFail, setNotifyFail, 'Notify on failure'],
              [notifyOk, setNotifyOk, 'Notify on success'],
            ].map(([val, setter, label]) => (
              <label key={label} style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer', fontSize: 13 }}>
                <input
                  type="checkbox"
                  checked={val}
                  onChange={e => setter(e.target.checked)}
                  style={{ width: 'auto', cursor: 'pointer', accentColor: 'var(--accent)' }}
                />
                {label}
              </label>
            ))}
          </div>

          {error && (
            <p style={{ color: 'var(--error-fg)', fontSize: 13 }}>{error}</p>
          )}
        </div>

        <div className="modal-footer">
          <button onClick={onClose} disabled={saving}>Cancel</button>
          <button className="primary" onClick={handleSave} disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : isDuplicate ? 'Create copy' : editing ? 'Save changes' : 'Create task'}
          </button>
        </div>
      </div>
    </div>
  )
}
