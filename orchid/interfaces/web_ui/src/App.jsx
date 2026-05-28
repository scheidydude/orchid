import { useState, useEffect, useRef } from 'react'
import Login from './components/Login.jsx'
import ProjectSwitcher from './components/ProjectSwitcher.jsx'
import TaskBoard from './components/TaskBoard.jsx'
import AgentStream from './components/AgentStream.jsx'
import RunControls from './components/RunControls.jsx'
import DecisionLog from './components/DecisionLog.jsx'
import SessionHistory from './components/SessionHistory.jsx'
import RecallSearch from './components/RecallSearch.jsx'
import HotMemory from './components/HotMemory.jsx'
import PlanningTab from './components/planning/PlanningTab.jsx'
import NewProjectWizard from './components/planning/NewProjectWizard.jsx'
import Settings from './components/Settings.jsx'
import ProjectSettings from './components/ProjectSettings.jsx'
import PMDashboard from './components/pm/PMDashboard.jsx'
import SchedulerTab from './components/SchedulerTab.jsx'
import { useProjects } from './hooks/useProjects.js'
import { useAgentStream } from './hooks/useAgentStream.js'
import { useMediaQuery } from './hooks/useMediaQuery.js'

const TABS = ['Tasks', 'Planning', 'PM', 'Stream', 'Decisions', 'Sessions', 'Recall', 'Memory', 'Config']

const TAB_SHORT = {
  Tasks: 'Tasks', Planning: 'Plan', PM: 'PM', Stream: 'Live',
  Decisions: 'Dec', Sessions: 'Hist', Recall: 'Recall',
  Memory: 'Mem', Config: 'Cfg',
}

// ── PageModal — full-screen overlay for global panels ────────────────────────

function PageModal({ title, onClose, children }) {
  // Close on Escape
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'var(--bg)',
      zIndex: 500,
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '10px 20px',
        background: 'var(--surface)',
        borderBottom: '1px solid var(--border)',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: 600, fontSize: 16, flex: 1 }}>{title}</span>
        <button
          onClick={onClose}
          style={{ fontSize: 16, padding: '4px 12px', color: 'var(--text-dim)' }}
          title="Close (Esc)"
        >
          ✕ Close
        </button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '24px 20px' }}>
        {children}
      </div>
    </div>
  )
}

// ── LogoutConfirm ─────────────────────────────────────────────────────────────

function LogoutConfirm({ onConfirm, onCancel }) {
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onCancel])

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: '#000b',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 600,
      }}
      onClick={e => e.target === e.currentTarget && onCancel()}
    >
      <div style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: '28px 32px',
        textAlign: 'center',
        maxWidth: 320,
        width: '90vw',
      }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>👋</div>
        <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Log out of Orchid?</div>
        <div style={{ color: 'var(--text-dim)', fontSize: 13, marginBottom: 24 }}>
          You'll need to sign in again to continue.
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
          <button className="danger" onClick={onConfirm} autoFocus>Log out</button>
          <button onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

// ── UserMenu ──────────────────────────────────────────────────────────────────

function UserMenu({ user, onSettings, onScheduler, onLogout }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Close on Escape
  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open])

  const action = (fn) => { setOpen(false); fn() }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          fontSize: 12,
          padding: '5px 12px',
          color: open ? 'var(--text)' : 'var(--text-dim)',
          borderColor: open ? 'var(--accent)' : undefined,
        }}
        title={`Signed in as ${user.username}`}
      >
        {user.username} {open ? '▲' : '▼'}
      </button>

      {open && (
        <div style={{
          position: 'absolute',
          top: 'calc(100% + 6px)',
          right: 0,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          boxShadow: '0 8px 24px #0008',
          minWidth: 180,
          zIndex: 400,
          overflow: 'hidden',
        }}>
          {/* User info header */}
          <div style={{
            padding: '10px 14px',
            borderBottom: '1px solid var(--border)',
            fontSize: 12,
            color: 'var(--text-dim)',
          }}>
            <div style={{ fontWeight: 600, color: 'var(--text)', marginBottom: 2 }}>
              {user.username}
            </div>
            {user.role && (
              <div style={{ textTransform: 'capitalize' }}>{user.role}</div>
            )}
          </div>

          {/* Menu items */}
          {[
            { icon: '⚙️', label: 'Settings',  fn: onSettings },
            { icon: '⏰', label: 'Scheduler', fn: onScheduler },
          ].map(item => (
            <button
              key={item.label}
              onClick={() => action(item.fn)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                width: '100%', textAlign: 'left',
                padding: '10px 14px', fontSize: 13,
                border: 'none', borderRadius: 0,
                background: 'transparent',
                color: 'var(--text)',
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'var(--surface2)'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
            </button>
          ))}

          <div style={{ borderTop: '1px solid var(--border)', margin: '4px 0' }} />

          <button
            onClick={() => action(onLogout)}
            style={{
              display: 'flex', alignItems: 'center', gap: 10,
              width: '100%', textAlign: 'left',
              padding: '10px 14px', fontSize: 13,
              border: 'none', borderRadius: 0,
              background: 'transparent',
              color: 'var(--error)',
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--surface2)'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
          >
            <span>↩</span>
            <span>Log out</span>
          </button>
        </div>
      )}
    </div>
  )
}

// ── App (auth gate) ───────────────────────────────────────────────────────────

export default function App() {
  const [user, setUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    fetch('/api/auth/me')
      .then(r => r.json())
      .then(d => { if (d.authenticated) setUser(d) })
      .catch(() => {})
      .finally(() => setAuthChecked(true))
  }, [])

  const handleLogout = async () => {
    await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {})
    setUser(null)
  }

  if (!authChecked) return null
  if (!user) return <Login onLogin={setUser} />
  return <AuthenticatedApp user={user} onLogout={handleLogout} />
}

// ── AuthenticatedApp ──────────────────────────────────────────────────────────

function AuthenticatedApp({ user, onLogout }) {
  const { projects, loading: projectsLoading, refresh: refreshProjects, newProjectIds } = useProjects()
  const [activeProject, setActiveProject] = useState(null)
  const [activeTab, setActiveTab] = useState('Tasks')
  const [orchidVersion, setOrchidVersion] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const isMobile = useMediaQuery('(max-width: 768px)')
  const panelBodyRef = useRef(null)
  const swipeTouchStartX = useRef(0)

  // Global panel state
  const [showSettings,      setShowSettings]      = useState(false)
  const [showScheduler,     setShowScheduler]     = useState(false)
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false)

  useEffect(() => {
    fetch('/api/version').then(r => r.json()).then(d => setOrchidVersion(d.version)).catch(() => {})
  }, [])

  const [showNewWizard, setShowNewWizard] = useState(false)
  const [planningBadge, setPlanningBadge] = useState(false)
  const { entries, runStatus, clear } = useAgentStream(activeProject)

  // Auto-select: honour ?p= URL param, else first project
  useEffect(() => {
    if (!activeProject && projects.length > 0) {
      const param = new URLSearchParams(window.location.search).get('p')
      const found = param && projects.find(p => p.id === param)
      setActiveProject(found ? found.id : projects[0].id)
    }
  }, [projects, activeProject])

  // Scroll panel body to top on tab switch
  useEffect(() => {
    if (panelBodyRef.current) panelBodyRef.current.scrollTop = 0
  }, [activeTab])

  const handleDrawerTouchStart = (e) => { swipeTouchStartX.current = e.touches[0].clientX }
  const handleDrawerTouchEnd   = (e) => {
    if (e.changedTouches[0].clientX - swipeTouchStartX.current < -50) setDrawerOpen(false)
  }

  const activeProjectData = projects.find(p => p.id === activeProject)

  const handleProjectSelect = (id) => {
    setActiveProject(id)
    setActiveTab('Tasks')
    setPlanningBadge(false)
    setDrawerOpen(false)
  }

  const handleRunChange = () => refreshProjects()

  const handleToggleActive = async (projectId, currentlyActive) => {
    try {
      await fetch(`/api/projects/${projectId}/active`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active: !currentlyActive }),
      })
      refreshProjects()
    } catch (err) {
      console.error('Failed to toggle project active state:', err)
    }
  }

  const handleProjectCreated = (projectId, _projectPath) => {
    setShowNewWizard(false)
    refreshProjects()
    setTimeout(() => {
      setActiveProject(projectId)
      setActiveTab('Planning')
    }, 500)
  }

  const handleLogoutConfirmed = async () => {
    setShowLogoutConfirm(false)
    onLogout()
  }

  return (
    <>
      <header className="app-header">
        <button
          className="hamburger-btn"
          onClick={() => setDrawerOpen(o => !o)}
          aria-label="Open project list"
        >
          ☰
        </button>
        <span className="logo">🌸 Orchid</span>
        {activeProjectData && (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
              <span style={{ color: 'var(--text-dim)', fontSize: 13 }}>
                {activeProjectData.name}
              </span>
              {activeProjectData.path && (
                <span
                  className="project-path"
                  title={activeProjectData.path}
                  style={{ fontSize: '0.75rem' }}
                >
                  {activeProjectData.path.replace(/^\/home\/[^/]+/, '~')}
                </span>
              )}
            </div>
            {activeProjectData.running && (
              <span style={{ fontSize: 12, color: '#56d364' }}>
                <span className="project-running-dot" />running
              </span>
            )}
          </>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className="header-project-count" style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            {projects.length} project{projects.length !== 1 ? 's' : ''}
          </span>
          <button
            className="primary"
            style={{ fontSize: 12, padding: '5px 12px' }}
            onClick={() => setShowNewWizard(true)}
          >
            <span className="new-project-label">+ New Project</span>
            <span className="new-project-short">+</span>
          </button>
          <UserMenu
            user={user}
            onSettings={() => setShowSettings(true)}
            onScheduler={() => setShowScheduler(true)}
            onLogout={() => setShowLogoutConfirm(true)}
          />
        </span>
      </header>

      <div className="app-body">
        {drawerOpen && (
          <div className="sidebar-backdrop" onClick={() => setDrawerOpen(false)} />
        )}
        <nav
          className={`sidebar${drawerOpen ? ' drawer-open' : ''}`}
          onTouchStart={handleDrawerTouchStart}
          onTouchEnd={handleDrawerTouchEnd}
        >
          {projectsLoading ? (
            <div className="loading" style={{ padding: '16px' }}>Loading…</div>
          ) : (
            <ProjectSwitcher
              projects={projects}
              activeId={activeProject}
              onSelect={handleProjectSelect}
              newProjectIds={newProjectIds}
              onToggleActive={handleToggleActive}
            />
          )}
          {orchidVersion && (
            <div className="sidebar-version" style={{
              position: 'absolute',
              bottom: 8, left: 10,
              fontSize: 10, color: 'var(--text-dim)',
              opacity: 0.5, pointerEvents: 'none', letterSpacing: '0.3px',
            }}>
              v{orchidVersion}
            </div>
          )}
        </nav>

        <div className="main-content">
          {activeProject ? (
            <>
              {activeTab !== 'Planning' && activeTab !== 'Config' && activeTab !== 'PM' && (
                <RunControls
                  projectId={activeProject}
                  runStatus={runStatus}
                  onRunChange={handleRunChange}
                />
              )}
              <div className="panel-tabs">
                {TABS.map(tab => (
                  <button
                    key={tab}
                    className={`panel-tab ${activeTab === tab ? 'active' : ''}`}
                    onClick={() => setActiveTab(tab)}
                  >
                    {isMobile ? TAB_SHORT[tab] : tab}
                    {tab === 'Stream' && entries.length > 0 && (
                      <span style={{ marginLeft: 5, fontSize: 10, background: 'var(--accent)', color: '#fff', borderRadius: 8, padding: '0 5px' }}>
                        {entries.length}
                      </span>
                    )}
                    {tab === 'Planning' && planningBadge && (
                      <span style={{ marginLeft: 5, fontSize: 10, background: 'var(--warning)', color: '#000', borderRadius: 8, padding: '0 5px' }}>
                        !
                      </span>
                    )}
                  </button>
                ))}
              </div>
              <div ref={panelBodyRef} className="panel-body" style={activeTab === 'Planning' ? { padding: 0, overflow: 'hidden' } : {}}>
                {activeTab === 'Tasks' && (
                  <TaskBoard projectId={activeProject} runStatus={runStatus} />
                )}
                {activeTab === 'PM' && (
                  <PMDashboard projectId={activeProject} />
                )}
                {activeTab === 'Planning' && (
                  <PlanningTab
                    projectId={activeProject}
                    runStatus={runStatus}
                    onSwitchToTasks={() => setActiveTab('Tasks')}
                  />
                )}
                {activeTab === 'Stream' && (
                  <AgentStream entries={entries} onClear={clear} />
                )}
                {activeTab === 'Decisions' && (
                  <DecisionLog projectId={activeProject} />
                )}
                {activeTab === 'Sessions' && (
                  <SessionHistory projectId={activeProject} />
                )}
                {activeTab === 'Recall' && (
                  <RecallSearch projectId={activeProject} />
                )}
                {activeTab === 'Memory' && (
                  <HotMemory projectId={activeProject} />
                )}
                {activeTab === 'Config' && (
                  <ProjectSettings projectId={activeProject} />
                )}
              </div>
            </>
          ) : (
            <div className="empty-state" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {projectsLoading ? 'Loading projects…' : 'Select a project or create one with + New Project'}
            </div>
          )}
        </div>
      </div>

      {/* ── Global modals ── */}

      {showSettings && (
        <PageModal title="⚙️ Settings" onClose={() => setShowSettings(false)}>
          <Settings />
        </PageModal>
      )}

      {showScheduler && (
        <PageModal title="⏰ Scheduler" onClose={() => setShowScheduler(false)}>
          <SchedulerTab />
        </PageModal>
      )}

      {showLogoutConfirm && (
        <LogoutConfirm
          onConfirm={handleLogoutConfirmed}
          onCancel={() => setShowLogoutConfirm(false)}
        />
      )}

      {showNewWizard && (
        <NewProjectWizard
          onCreated={handleProjectCreated}
          onClose={() => setShowNewWizard(false)}
        />
      )}
    </>
  )
}
