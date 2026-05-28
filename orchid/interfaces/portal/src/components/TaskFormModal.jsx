import { useState, useEffect, useRef } from 'react'
import CronBuilder from './CronBuilder.jsx'
import MCPToolPicker from './MCPToolPicker.jsx'

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

function downloadJSON(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export default function TaskFormModal({ initial, onSave, onTest, onToast, onClose, isDuplicate = false }) {
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
  const [showCronBuilder, setShowCronBuilder] = useState(false)
  const [showMCPPicker, setShowMCPPicker]   = useState(false)
  const [testStatus, setTestStatus] = useState(null) // null | 'running' | 'dispatched' | 'error'
  // Track task_id after a save-and-test so repeat tests don't create duplicates
  const [savedTaskId, setSavedTaskId] = useState(editing ? (initial?.task_id ?? null) : null)
  const importRef = useRef(null)

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
    const h = (e) => { if (e.key === 'Escape' && !showCronBuilder) onClose() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose, showCronBuilder])

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

  const handleTest = async () => {
    setError('')
    let taskId = savedTaskId

    // New/duplicate task that hasn't been saved yet — save first
    if (!taskId) {
      if (!name.trim()) { setError('Name is required to save & test'); return }
      let config
      try { config = JSON.parse(configStr) }
      catch { setError('Config is not valid JSON'); return }

      setSaving(true)
      try {
        const saved = await onSave({
          name: name.trim(),
          description: desc.trim(),
          schedule,
          task_type: taskType,
          config,
          enabled,
          notify_on_failure: notifyFail,
          notify_on_success: notifyOk,
        })
        taskId = saved?.task_id
        setSavedTaskId(taskId)
      } catch (e) {
        setError(e.message || 'Save failed')
        setSaving(false)
        return
      }
      setSaving(false)
    }

    if (!taskId || !onTest) return

    setTestStatus('running')
    try {
      await onTest(taskId)
      setTestStatus('dispatched')
      onToast?.('Task dispatched ▶')
      // Close modal after toast duration
      setTimeout(() => onClose(), 3000)
    } catch (e) {
      setTestStatus('error')
      setError(e.message || 'Test dispatch failed')
    }
  }

  const handleExport = () => {
    let config
    try { config = JSON.parse(configStr) } catch { config = configStr }
    const data = {
      name: name.trim(),
      description: desc.trim(),
      schedule,
      task_type: taskType,
      config,
      enabled,
      notify_on_failure: notifyFail,
      notify_on_success: notifyOk,
    }
    const filename = `${(name.trim() || 'task').replace(/\s+/g, '_')}.orchid-task.json`
    downloadJSON(data, filename)
  }

  const handleImport = (e) => {
    const file = e.target.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const data = JSON.parse(ev.target.result)
        if (data.name)        setName(data.name)
        if (data.description !== undefined) setDesc(data.description)
        if (data.schedule)    setSchedule(data.schedule)
        if (data.task_type && BLANK_CONFIGS[data.task_type]) setTaskType(data.task_type)
        if (data.config)      setConfigStr(JSON.stringify(data.config, null, 2))
        if (data.notify_on_failure !== undefined) setNotifyFail(data.notify_on_failure)
        if (data.notify_on_success !== undefined) setNotifyOk(data.notify_on_success)
        if (data.enabled !== undefined) setEnabled(data.enabled)
        setError('')
      } catch {
        setError('Could not parse JSON file')
      }
    }
    reader.readAsText(file)
    e.target.value = ''
  }

  return (
    <>
      <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
        <div className="modal" style={{ maxWidth: 900, width: '95vw' }}>
          <div className="modal-header">
            <span className="modal-title">{isDuplicate ? '⧉ Duplicate Task' : editing ? '✏️ Edit Task' : '＋ New Scheduled Task'}</span>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              {/* Import */}
              <input
                ref={importRef}
                type="file"
                accept=".json,.orchid-task.json"
                onChange={handleImport}
                style={{ display: 'none' }}
              />
              <button
                type="button"
                className="ghost"
                style={{ fontSize: 12, padding: '3px 10px' }}
                onClick={() => importRef.current?.click()}
                title="Import task from JSON file"
              >
                ↑ Import
              </button>
              {/* Export */}
              <button
                type="button"
                className="ghost"
                style={{ fontSize: 12, padding: '3px 10px' }}
                onClick={handleExport}
                title="Export task as JSON file"
              >
                ↓ Export
              </button>
              <button className="ghost icon" onClick={onClose}>✕</button>
            </div>
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
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <select value={taskType} onChange={e => setTaskType(e.target.value)} style={{ flex: 1 }}>
                  <option value="agent_prompt">Agent prompt</option>
                  <option value="agent_tool">Agent tool (MCP)</option>
                  <option value="mcp_tool">MCP tool (single call)</option>
                  <option value="shell">Shell command</option>
                </select>
                {(taskType === 'mcp_tool' || taskType === 'agent_tool') && (
                  <button
                    type="button"
                    style={{
                      padding: '6px 12px', fontSize: 12, whiteSpace: 'nowrap',
                      borderColor: 'var(--accent)', color: 'var(--accent)',
                      background: 'color-mix(in srgb, var(--accent) 8%, transparent)',
                    }}
                    onClick={() => setShowMCPPicker(true)}
                  >
                    🔌 Browse MCP
                  </button>
                )}
              </div>
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
                <button
                  type="button"
                  style={{
                    padding: '3px 10px', fontSize: 11,
                    borderRadius: 4,
                    background: 'var(--surface2)',
                    borderColor: 'var(--accent)',
                    color: 'var(--accent)',
                    fontWeight: 600,
                  }}
                  onClick={() => setShowCronBuilder(true)}
                >
                  🗓 Schedule builder
                </button>
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
                rows={12}
                style={{ fontFamily: 'var(--mono)', fontSize: 12, resize: 'vertical', minHeight: 160 }}
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
            <button
              type="button"
              onClick={handleTest}
              disabled={saving || testStatus === 'running' || !name.trim()}
              style={{ marginRight: 'auto' }}
            >
              {testStatus === 'running'     ? 'Testing…'
              : testStatus === 'dispatched' ? '✓ Dispatched'
              : (editing || savedTaskId)    ? '▶ Test'
              : '▶ Save & Test'}
            </button>
            <button onClick={onClose} disabled={saving}>Cancel</button>
            <button className="primary" onClick={handleSave} disabled={saving || !name.trim()}>
              {saving ? 'Saving…' : isDuplicate ? 'Create copy' : editing ? 'Save changes' : 'Create task'}
            </button>
          </div>
        </div>
      </div>

      {showCronBuilder && (
        <CronBuilder
          onApply={(cron) => { setSchedule(cron); setShowCronBuilder(false) }}
          onClose={() => setShowCronBuilder(false)}
        />
      )}

      {showMCPPicker && (
        <MCPToolPicker
          taskType={taskType}
          currentConfig={(() => { try { return JSON.parse(configStr) } catch { return {} } })()}
          onApply={(cfg) => setConfigStr(JSON.stringify(cfg, null, 2))}
          onClose={() => setShowMCPPicker(false)}
        />
      )}
    </>
  )
}
