import { useState } from 'react'
import { useAuth } from './hooks/useAuth.js'
import Users    from './pages/Users.jsx'
import MCPCatalog from './pages/MCPCatalog.jsx'
import AuditLog from './pages/AuditLog.jsx'
import Quotas   from './pages/Quotas.jsx'

const PAGES = [
  { id: 'users',    label: 'Users' },
  { id: 'mcp',      label: 'MCP Catalog' },
  { id: 'audit',    label: 'Audit Log' },
  { id: 'quotas',   label: 'Quotas' },
]

// ── Login ─────────────────────────────────────────────────────────────────────

function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const r = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || 'Login failed')
        return
      }
      const d = await r.json()
      onLogin(d)
    } catch {
      setError('Network error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      minHeight: '100vh', background: 'var(--bg)',
    }}>
      <div style={{ width: 320 }}>
        <div style={{ textAlign: 'center', marginBottom: 28 }}>
          <span style={{ fontSize: 28 }}>🌸</span>
          <div style={{ fontWeight: 700, fontSize: 18, marginTop: 8 }}>Orchid Admin</div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 4 }}>Admin access only</div>
        </div>
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="field">
            <label>Username</label>
            <input
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoFocus required
              autoComplete="username"
            />
          </div>
          <div className="field">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </div>
          {error && <p style={{ color: 'var(--error-fg)', fontSize: 13 }}>{error}</p>}
          <button type="submit" className="primary" disabled={loading}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}

// ── Header ────────────────────────────────────────────────────────────────────

function Header({ user, page, setPage, onLogout }) {
  return (
    <header className="app-header">
      <span className="logo">
        🌸 Orchid
        <span className="logo-admin">ADMIN</span>
      </span>

      <nav className="nav-tabs" style={{ flex: 1 }}>
        {PAGES.map(p => (
          <button
            key={p.id}
            className={`nav-tab${page === p.id ? ' active' : ''}`}
            onClick={() => setPage(p.id)}
          >
            {p.label}
          </button>
        ))}
      </nav>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <a href="/app/" style={{ fontSize: 12, color: 'var(--text-dim)' }}>
          ↗ User portal
        </a>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{user.username}</span>
        <button className="ghost" style={{ fontSize: 12, padding: '4px 10px' }} onClick={onLogout}>
          Sign out
        </button>
      </div>
    </header>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const { user, checked, setUser, logout } = useAuth()
  const [page, setPage] = useState('users')

  if (!checked) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <div className="spinner" />
      </div>
    )
  }

  if (!user) return <Login onLogin={setUser} />

  // Non-admin lands here somehow → redirect to portal
  if (user.role !== 'admin') {
    window.location.href = '/app/'
    return null
  }

  const pageComponents = {
    users:  <Users />,
    mcp:    <MCPCatalog />,
    audit:  <AuditLog />,
    quotas: <Quotas />,
  }

  return (
    <>
      <Header user={user} page={page} setPage={setPage} onLogout={logout} />
      <main>{pageComponents[page]}</main>
    </>
  )
}
