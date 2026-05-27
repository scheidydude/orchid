export function StatusBadge({ status }) {
  if (!status) return <span className="badge badge-idle">never run</span>
  const cls = {
    success: 'badge-success',
    failure: 'badge-error',
    error:   'badge-error',
    timeout: 'badge-warning',
    running: 'badge-running',
  }[status] || 'badge-idle'
  return <span className={`badge ${cls}`}>{status}</span>
}

export function TypeBadge({ type }) {
  const labels = { agent_prompt: 'Agent', mcp_tool: 'MCP', shell: 'Shell' }
  return <span className="badge badge-type">{labels[type] || type}</span>
}

export function RoleBadge({ role }) {
  if (role === 'admin') return (
    <span className="badge" style={{ color: '#f0883e', background: '#f0883e22' }}>admin</span>
  )
  if (role === 'readonly') return (
    <span className="badge badge-idle">read-only</span>
  )
  return null
}
