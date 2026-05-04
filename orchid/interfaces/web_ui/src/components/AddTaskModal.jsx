import { useState } from 'react'

const TYPES = ['draft', 'code_generate', 'orchestrate', 'review', 'plan', 'search', 'summarize', 'transform']

export default function AddTaskModal({ projectId, onClose, onCreated }) {
  const [form, setForm] = useState({ title: '', type: 'draft', priority: 2, depends_on: '', model: '', description: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const submit = async (e) => {
    e.preventDefault()
    if (!form.title.trim()) { setError('Title is required'); return }
    setLoading(true)
    try {
      const body = {
        title: form.title.trim(),
        type: form.type,
        priority: parseInt(form.priority, 10),
        depends_on: form.depends_on ? form.depends_on.split(',').map(s => s.trim()).filter(Boolean) : [],
        model: form.model || null,
        description: form.description.trim(),
      }
      const res = await fetch(`/api/projects/${projectId}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const task = await res.json()
      onCreated(task)
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <h3>Add Task</h3>
        <form onSubmit={submit}>
          <div className="modal-field">
            <label>Title *</label>
            <input autoFocus value={form.title} onChange={e => set('title', e.target.value)} placeholder="Task description..." />
          </div>
          <div className="modal-grid-2col">
            <div className="modal-field">
              <label>Type</label>
              <select value={form.type} onChange={e => set('type', e.target.value)}>
                {TYPES.map(t => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div className="modal-field">
              <label>Priority</label>
              <select value={form.priority} onChange={e => set('priority', e.target.value)}>
                <option value={1}>1 — High</option>
                <option value={2}>2 — Normal</option>
                <option value={3}>3 — Low</option>
              </select>
            </div>
          </div>
          <div className="modal-grid-2col">
            <div className="modal-field">
              <label>Depends on (comma-separated)</label>
              <input value={form.depends_on} onChange={e => set('depends_on', e.target.value)} placeholder="T001,T002" />
            </div>
            <div className="modal-field">
              <label>Model override</label>
              <select value={form.model} onChange={e => set('model', e.target.value)}>
                <option value="">auto</option>
                <option value="claude">claude</option>
                <option value="local">local</option>
              </select>
            </div>
          </div>
          <div className="modal-field">
            <label>Description (optional)</label>
            <textarea rows={3} value={form.description} onChange={e => set('description', e.target.value)} placeholder="Additional context..." />
          </div>
          {error && <div className="error-msg">{error}</div>}
          <div className="modal-actions">
            <button type="button" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary" disabled={loading}>
              {loading ? 'Adding…' : 'Add Task'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
