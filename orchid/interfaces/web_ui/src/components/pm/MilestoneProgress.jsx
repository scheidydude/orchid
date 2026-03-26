import { useState, useEffect } from 'react'

function groupByMilestone(tasks) {
  const nonRollup = tasks.filter(t => t.type !== 'rollup')
  const rollups = tasks.filter(t => t.type === 'rollup')

  if (rollups.length === 0) {
    // No milestones defined — show everything as a single summary
    return [{ name: 'All Tasks (no milestones defined)', tasks: nonRollup, noMilestones: true }]
  }

  const milestones = []
  const assigned = new Set()
  for (const rollup of rollups) {
    const sources = rollup.depends_on || []
    const members = tasks.filter(t => sources.includes(t.id))
    members.forEach(t => assigned.add(t.id))
    milestones.push({ name: rollup.title, tasks: members, rollup })
  }

  const ungrouped = nonRollup.filter(t => !assigned.has(t.id))
  if (ungrouped.length > 0) {
    milestones.push({ name: `Ungrouped tasks (${ungrouped.length} not in any milestone)`, tasks: ungrouped })
  }

  return milestones
}

function MilestoneCard({ milestone }) {
  const { tasks } = milestone
  const total = tasks.length
  if (total === 0) return null

  const done = tasks.filter(t => t.status === 'DONE').length
  const blocked = tasks.filter(t => t.status === 'BLOCKED').length
  const skipped = tasks.filter(t => t.status === 'SKIPPED').length
  const pending = total - done - blocked - skipped
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <div style={{ marginBottom: 16, padding: '12px 16px', background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>{milestone.name}</span>
        <span style={{ fontSize: 13, color: pct === 100 ? '#56d364' : 'var(--text-dim)' }}>{pct}%</span>
      </div>

      {/* Progress bar */}
      <div style={{ height: 6, background: 'var(--bg)', borderRadius: 3, overflow: 'hidden', marginBottom: 8 }}>
        <div style={{ height: '100%', width: `${pct}%`, background: '#56d364', borderRadius: 3, transition: 'width 0.3s' }} />
      </div>

      {/* Counts */}
      <div style={{ display: 'flex', gap: 12, fontSize: 12, color: 'var(--text-dim)' }}>
        <span title="Done">✅ {done}</span>
        <span title="Pending">⏳ {pending}</span>
        {blocked > 0 && <span title="Blocked" style={{ color: '#f85149' }}>❌ {blocked}</span>}
        {skipped > 0 && <span title="Skipped">⤼ {skipped}</span>}
        <span style={{ marginLeft: 'auto' }}>{total} tasks</span>
      </div>
    </div>
  )
}

export default function MilestoneProgress({ projectId }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!projectId) return
    setLoading(true)
    fetch(`/api/projects/${projectId}/tasks`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(data => { setTasks(data); setError(null) })
      .catch(err => setError(String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <div className="loading" style={{ padding: 12 }}>Loading tasks…</div>
  if (error) return <div className="error-msg">Error: {error}</div>
  if (tasks.length === 0) return <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: 12 }}>No tasks found.</div>

  const milestones = groupByMilestone(tasks)
  return (
    <div>
      {milestones.map((m, i) => <MilestoneCard key={i} milestone={m} />)}
    </div>
  )
}
