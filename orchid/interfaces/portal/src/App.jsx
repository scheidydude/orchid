import { useState, useEffect, useRef } from 'react'
import { useAuth } from './hooks/useAuth.js'
import { useScheduledTasks } from './hooks/useScheduledTasks.js'
import { useProjects } from './hooks/useProjects.js'
import Login from './components/Login.jsx'
import Dashboard from './components/Dashboard.jsx'
import UserSettings from './components/UserSettings.jsx'
import { RoleBadge } from './components/StatusBadge.jsx'

// ── Invite token detection ────────────────────────────────────────────────────
// Check URL for ?invite_id=…&invite_token=… before rendering auth flow

function _parseInviteParams() {
  const qs = new URLSearchParams(window.location.search)
  const id = qs.get('invite_id')
  const token = qs.get('invite_token')
  return id && token ? { id, token } : null
}

// ── UserMenu ──────────────────────────────────────────────────────────────────

function UserMenu({ user, onSettings, onLogout }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  useEffect(() => {
    if (!open) return
    const h = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [open])

  const action = (fn) => { setOpen(false); fn() }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        className="ghost"
        onClick={() => setOpen(o => !o)}
        style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}
      >
        <span style={{
          width: 26, height: 26, borderRadius: '50%',
          background: 'var(--accent)', color: '#fff',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 12, fontWeight: 700, flexShrink: 0,
        }}>
          {(user.username || '?')[0].toUpperCase()}
        </span>
        <span style={{ color: 'var(--text-dim)' }}>{user.username}</span>
        <span style={{ fontSize: 10, color: 'var(--text-mute)' }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 8px)', right: 0,
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)',
          minWidth: 200, zIndex: 400, overflow: 'hidden',
        }}>
          {/* Header */}
          <div style={{
            padding: '12px 16px', borderBottom: '1px solid var(--border)',
          }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{user.username}</div>
            {user.email && <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2 }}>{user.email}</div>}
            <div style={{ marginTop: 6 }}><RoleBadge role={user.role} /></div>
          </div>

          {/* Items */}
          {[
            { icon: '⚙️', label: 'Settings', fn: onSettings },
          ].map(item => (
            <button
              key={item.label}
              className="ghost"
              onClick={() => action(item.fn)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                width: '100%', textAlign: 'left',
                padding: '10px 16px', fontSize: 13,
                border: 'none', borderRadius: 0,
              }}
            >
              <span>{item.icon}</span><span>{item.label}</span>
            </button>
          ))}

          {/* Admin console link — visible to admins */}
          {user.role === 'admin' && (
            <a
              href="/"
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '10px 16px', fontSize: 13, color: 'var(--text)',
                textDecoration: 'none',
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'var(--surface2)'}
              onMouseLeave={e => e.currentTarget.style.background = ''}
            >
              <span>🛠</span><span>Admin Console</span>
            </a>
          )}

          <div style={{ borderTop: '1px solid var(--border)', margin: '4px 0' }} />

          <button
            className="ghost"
            onClick={() => action(onLogout)}
            style={{
              display: 'flex', alignItems: 'center', gap: 10,
              width: '100%', textAlign: 'left',
              padding: '10px 16px', fontSize: 13,
              border: 'none', borderRadius: 0,
              color: 'var(--error-fg)',
            }}
          >
            <span>↩</span><span>Log out</span>
          </button>
        </div>
      )}
    </div>
  )
}

// ── Header ────────────────────────────────────────────────────────────────────

function Header({ user, view, onView, onLogout }) {
  const NAV = [
    { id: 'dashboard', label: '🏠 Dashboard' },
    { id: 'settings',  label: '⚙️ Settings' },
  ]

  return (
    <header className="app-header">
      <a href="/app/" className="logo" style={{ textDecoration: 'none' }}>
        🌸 Orchid
      </a>

      <nav className="nav-tabs" style={{ flex: 1 }}>
        {NAV.map(n => (
          <button
            key={n.id}
            className={`nav-tab${view === n.id ? ' active' : ''}`}
            onClick={() => onView(n.id)}
          >
            {n.label}
          </button>
        ))}
      </nav>

      <UserMenu user={user} onSettings={() => onView('settings')} onLogout={onLogout} />
    </header>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const inviteParams = _parseInviteParams()

  // If invite params are present in URL, show the accept-invite flow
  // regardless of auth state (user doesn't have an account yet)
  if (inviteParams) {
    return <AcceptInvite inviteId={inviteParams.id} inviteToken={inviteParams.token} />
  }

  return <AuthedApp />
}

function AuthedApp() {
  const { user, checked, setUser, logout } = useAuth()

  if (!checked) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span className="spinner" style={{ width: 24, height: 24 }} />
      </div>
    )
  }

  if (!user) return <Login onLogin={setUser} />

  return <PortalApp user={user} onLogout={logout} />
}

// ── AcceptInvite ──────────────────────────────────────────────────────────────

function AcceptInvite({ inviteId, inviteToken }) {
  const [email, setEmail]       = useState(null)
  const [validating, setValidating] = useState(true)
  const [invalid, setInvalid]   = useState(false)
  const [expired, setExpired]   = useState(false)

  const [password, setPassword]   = useState('')
  const [confirm, setConfirm]     = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError]         = useState('')
  const [done, setDone]           = useState(false)

  // Validate token on mount
  useEffect(() => {
    fetch(`/api/auth/invite/${encodeURIComponent(inviteId)}`)
      .then(r => {
        if (r.status === 410) { setExpired(true); return null }
        if (!r.ok)            { setInvalid(true); return null }
        return r.json()
      })
      .then(d => { if (d) setEmail(d.email) })
      .catch(() => setInvalid(true))
      .finally(() => setValidating(false))
  }, [inviteId])

  const handleAccept = async (e) => {
    e.preventDefault()
    setError('')
    if (password !== confirm) { setError('Passwords do not match'); return }
    if (password.length < 8)  { setError('Password must be at least 8 characters'); return }
    setSubmitting(true)
    try {
      const r = await fetch('/api/auth/invite/accept', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token_id: inviteId, invite_token: inviteToken, password }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || 'Activation failed')
        return
      }
      setDone(true)
      // Remove invite params from URL so a refresh doesn't re-trigger this flow
      window.history.replaceState({}, '', '/app/')
      // Reload after short delay so cookie is picked up
      setTimeout(() => window.location.reload(), 1200)
    } catch {
      setError('Network error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg)',
    }}>
      <div style={{
        width: '100%', maxWidth: 400,
        background: 'var(--surface)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius-lg)', padding: '32px 28px',
        boxShadow: 'var(--shadow)',
      }}>
        <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 6 }}>🌸 Orchid</div>

        {validating && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-dim)', fontSize: 14, marginTop: 20 }}>
            <span className="spinner" style={{ width: 16, height: 16 }} />
            Validating invite link…
          </div>
        )}

        {!validating && expired && (
          <div style={{ marginTop: 20 }}>
            <p style={{ color: 'var(--error-fg)', fontSize: 14 }}>
              ⏱ This invite link has expired (48-hour limit).
            </p>
            <p style={{ color: 'var(--text-dim)', fontSize: 13, marginTop: 8 }}>
              Ask your admin to send a new invite.
            </p>
          </div>
        )}

        {!validating && invalid && (
          <div style={{ marginTop: 20 }}>
            <p style={{ color: 'var(--error-fg)', fontSize: 14 }}>
              ✗ Invalid or already-used invite link.
            </p>
            <p style={{ color: 'var(--text-dim)', fontSize: 13, marginTop: 8 }}>
              If you've already set your password, <a href="/app/" style={{ color: 'var(--accent)' }}>log in here</a>.
            </p>
          </div>
        )}

        {!validating && email && !done && (
          <>
            <p style={{ color: 'var(--text-dim)', fontSize: 13, margin: '16px 0 24px' }}>
              You've been invited to Orchid. Set a password for <strong>{email}</strong> to activate your account.
            </p>
            <form onSubmit={handleAccept} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div className="field">
                <label>Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  autoComplete="new-password"
                  required
                  minLength={8}
                  autoFocus
                />
                <span className="hint">Minimum 8 characters</span>
              </div>
              <div className="field">
                <label>Confirm password</label>
                <input
                  type="password"
                  value={confirm}
                  onChange={e => setConfirm(e.target.value)}
                  autoComplete="new-password"
                  required
                />
              </div>
              {error && <p style={{ color: 'var(--error-fg)', fontSize: 13 }}>{error}</p>}
              <button type="submit" className="primary" disabled={submitting}>
                {submitting ? 'Activating…' : 'Activate account'}
              </button>
            </form>
          </>
        )}

        {done && (
          <div style={{ marginTop: 20, textAlign: 'center' }}>
            <p style={{ color: 'var(--success-fg)', fontSize: 14, fontWeight: 600 }}>
              ✓ Account activated! Logging you in…
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

// ── PortalApp ─────────────────────────────────────────────────────────────────

function PortalApp({ user, onLogout }) {
  const [view, setView] = useState('dashboard')

  const taskHook    = useScheduledTasks()
  const projectHook = useProjects()

  const taskOps = {
    runNow:     taskHook.runNow,
    deleteTask: taskHook.deleteTask,
    createTask: taskHook.createTask,
    updateTask: taskHook.updateTask,
    getRuns:    taskHook.getRuns,
    refresh:    taskHook.refresh,
  }

  return (
    <>
      <Header user={user} view={view} onView={setView} onLogout={onLogout} />

      {view === 'dashboard' && (
        <Dashboard
          tasks={taskHook.tasks}
          tasksLoading={taskHook.loading}
          projects={projectHook.projects}
          projectsLoading={projectHook.loading}
          taskOps={taskOps}
        />
      )}

      {view === 'settings' && (
        <UserSettings user={user} />
      )}
    </>
  )
}
