import { useState, useEffect, useRef } from 'react'
import { useAuth } from './hooks/useAuth.js'
import { useScheduledTasks } from './hooks/useScheduledTasks.js'
import { useProjects } from './hooks/useProjects.js'
import Login from './components/Login.jsx'
import Dashboard from './components/Dashboard.jsx'
import UserSettings from './components/UserSettings.jsx'
import { RoleBadge } from './components/StatusBadge.jsx'

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
  const { user, checked, setUser, logout } = useAuth()

  // If admin and not deliberately on portal, redirect to main app
  useEffect(() => {
    if (!checked || !user) return
    // Admins can stay in portal (they may have navigated here deliberately)
    // No forced redirect — admin sees user view as a feature
  }, [checked, user])

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
