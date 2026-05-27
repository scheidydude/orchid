import { useState, useEffect, useCallback } from 'react'

// ── Inline edit cell ──────────────────────────────────────────────────────────

function EditableNumber({ value, onSave, min = 0, placeholder = '0 = unlimited' }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal]         = useState(String(value ?? 0))
  const [saving, setSaving]   = useState(false)

  const commit = async () => {
    const num = parseFloat(val)
    if (isNaN(num) || num < 0) { setEditing(false); setVal(String(value ?? 0)); return }
    setSaving(true)
    await onSave(num)
    setSaving(false)
    setEditing(false)
  }

  if (editing) {
    return (
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <input
          type="number"
          min={min}
          step="0.01"
          value={val}
          onChange={e => setVal(e.target.value)}
          style={{ width: 90 }}
          autoFocus
          onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setEditing(false); setVal(String(value ?? 0)) } }}
        />
        <button style={{ padding: '3px 8px', fontSize: 11 }} disabled={saving} onClick={commit}>
          {saving ? '…' : '✓'}
        </button>
        <button className="ghost" style={{ padding: '3px 8px', fontSize: 11 }} onClick={() => { setEditing(false); setVal(String(value ?? 0)) }}>
          ✕
        </button>
      </div>
    )
  }

  const display = value === 0 ? <em style={{ color: 'var(--text-mute)' }}>unlimited</em> : value
  return (
    <span
      style={{ cursor: 'pointer', borderBottom: '1px dashed var(--border)', paddingBottom: 1 }}
      title="Click to edit"
      onClick={() => { setVal(String(value ?? 0)); setEditing(true) }}
    >
      {display}
    </span>
  )
}

// ── Usage bar ─────────────────────────────────────────────────────────────────

function UsageBar({ used, limit }) {
  if (!limit || limit <= 0) {
    return (
      <span style={{ fontSize: 11, color: 'var(--text-mute)' }}>
        ${used?.toFixed(4) ?? '0.0000'} used
      </span>
    )
  }
  const pct = Math.min(100, (used / limit) * 100)
  const color = pct >= 90 ? 'var(--error-fg)' : pct >= 70 ? 'var(--warn-fg)' : 'var(--info-fg)'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <div style={{ fontSize: 11, color }}>
        ${used?.toFixed(4) ?? '0.0000'} / ${limit.toFixed(2)}
        <span style={{ color: 'var(--text-mute)', marginLeft: 6 }}>{pct.toFixed(0)}%</span>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: 'var(--border)', width: 100, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 2 }} />
      </div>
    </div>
  )
}

// ── Quotas page ───────────────────────────────────────────────────────────────

export default function Quotas() {
  const [users, setUsers]     = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState('')
  const [search, setSearch]   = useState('')
  const [resetting, setResetting] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/auth/users')
      if (!r.ok) { setError('Failed to load users'); return }
      const d = await r.json()
      setUsers(d.users?.filter(u => u.is_active) || [])
    } catch { setError('Network error') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const update = async (userId, field, value) => {
    try {
      const r = await fetch(`/api/auth/users/${userId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value }),
      })
      if (r.ok) {
        setUsers(us => us.map(u =>
          u.user_id === userId ? { ...u, [field]: value } : u
        ))
      }
    } catch { /* ignore */ }
  }

  const resetBudget = async (userId) => {
    if (!confirm('Reset this user\'s budget usage to $0.00?')) return
    setResetting(userId)
    try {
      const r = await fetch(`/api/admin/users/${userId}/budget/reset`, { method: 'POST' })
      if (r.ok) {
        setUsers(us => us.map(u =>
          u.user_id === userId ? { ...u, budget_used_usd: 0 } : u
        ))
      }
    } catch { /* ignore */ }
    finally { setResetting(null) }
  }

  const filtered = users.filter(u =>
    !search ||
    u.username.includes(search) ||
    u.user_id.includes(search)
  )

  return (
    <div className="page">
      <div className="section-header">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700 }}>Quotas</h2>
          <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 4 }}>
            Set per-user LLM spend and CPU limits. 0 = unlimited.
          </p>
        </div>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search users…"
          style={{ width: 200 }}
        />
      </div>

      {error && <p style={{ color: 'var(--error-fg)', marginBottom: 14 }}>{error}</p>}

      {loading ? (
        <div style={{ display: 'flex', gap: 10, padding: 24, color: 'var(--text-dim)', alignItems: 'center' }}>
          <div className="spinner" /> Loading…
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th>LLM Budget (USD)</th>
                  <th>LLM Usage</th>
                  <th>CPU budget (s/day)</th>
                  <th>CPU today</th>
                  <th style={{ width: 80 }}></th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-mute)', padding: 32 }}>
                      No active users found
                    </td>
                  </tr>
                ) : filtered.map(u => (
                  <tr key={u.user_id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{u.username}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>
                        {u.user_id}
                      </div>
                    </td>
                    <td>
                      <span style={{
                        fontSize: 11, fontWeight: 600,
                        color: u.role === 'admin' ? 'var(--warn-fg)' : 'var(--info-fg)',
                      }}>
                        {u.role}
                      </span>
                    </td>
                    <td>
                      <EditableNumber
                        value={u.budget_usd}
                        onSave={v => update(u.user_id, 'budget_usd', v)}
                        min={0}
                        placeholder="0 = unlimited"
                      />
                      {u.budget_usd > 0 && (
                        <span style={{ fontSize: 11, color: 'var(--text-mute)', marginLeft: 8 }}>USD</span>
                      )}
                    </td>
                    <td>
                      <UsageBar used={u.budget_used_usd ?? 0} limit={u.budget_usd} />
                    </td>
                    <td>
                      <EditableNumber
                        value={u.cpu_budget_seconds}
                        onSave={v => update(u.user_id, 'cpu_budget_seconds', v)}
                        min={0}
                        placeholder="0 = unlimited"
                      />
                      {u.cpu_budget_seconds > 0 && (
                        <span style={{ fontSize: 11, color: 'var(--text-mute)', marginLeft: 8 }}>sec/day</span>
                      )}
                    </td>
                    <td>
                      <UsageBar used={u.cpu_used_seconds ?? 0} limit={u.cpu_budget_seconds} />
                    </td>
                    <td>
                      <button
                        className="ghost"
                        style={{ fontSize: 11, padding: '3px 8px' }}
                        disabled={resetting === u.user_id || (u.budget_used_usd ?? 0) === 0}
                        title="Reset usage counter to $0.00"
                        onClick={() => resetBudget(u.user_id)}
                      >
                        {resetting === u.user_id ? '…' : 'Reset'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text-mute)' }}>
            {filtered.length} active user{filtered.length !== 1 ? 's' : ''}
            &nbsp;·&nbsp;Click any value to edit. Enter to save, Esc to cancel.
          </div>
        </div>
      )}
    </div>
  )
}
