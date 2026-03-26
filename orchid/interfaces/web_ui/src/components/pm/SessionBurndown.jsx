import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts'

function parseSessionEntries(entries) {
  let done = 0, blocked = 0, skipped = 0
  for (const e of entries) {
    const type = e.type || ''
    if (type === 'task_done') done++
    else if (type === 'task_failed' || type === 'task_error') blocked++
    else if (e.status === 'SKIPPED') skipped++
  }
  return { done, blocked, skipped }
}

function sessionLabel(sid) {
  // session_20260325_120000 → 03/25 12:00
  const m = sid.match(/session_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/)
  if (!m) return sid.replace('session_', '')
  return `${m[2]}/${m[3]} ${m[4]}:${m[5]}`
}

export default function SessionBurndown({ projectId }) {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!projectId) return
    setLoading(true)
    setData([])

    fetch(`/api/projects/${projectId}/sessions`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(async (sessions) => {
        // Fetch up to 10 most recent sessions
        const recent = sessions.slice(0, 10).reverse()
        const rows = await Promise.all(
          recent.map(async (s) => {
            try {
              const res = await fetch(`/api/projects/${projectId}/sessions/${s.id}`)
              if (!res.ok) return null
              const detail = await res.json()
              const counts = parseSessionEntries(detail.entries || [])
              return { name: sessionLabel(s.id), ...counts }
            } catch {
              return null
            }
          })
        )
        setData(rows.filter(Boolean))
        setError(null)
      })
      .catch(err => setError(String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) return <div className="loading" style={{ padding: 12 }}>Loading session data…</div>
  if (error) return <div className="error-msg">Error: {error}</div>
  if (data.length === 0) return <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: 12 }}>No session logs found.</div>

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 24 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
        <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#8b949e' }} angle={-30} textAnchor="end" />
        <YAxis tick={{ fontSize: 11, fill: '#8b949e' }} allowDecimals={false} />
        <Tooltip
          contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: '#c9d1d9' }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="done" name="Completed" fill="#56d364" stackId="a" />
        <Bar dataKey="blocked" name="Blocked" fill="#f85149" stackId="a" />
        <Bar dataKey="skipped" name="Skipped" fill="#388bfd" stackId="a" />
      </BarChart>
    </ResponsiveContainer>
  )
}
