import { useState, useEffect } from 'react'
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
import { useProjects } from './hooks/useProjects.js'
import { useAgentStream } from './hooks/useAgentStream.js'

const TABS = ['Tasks', 'Planning', 'PM', 'Stream', 'Decisions', 'Sessions', 'Recall', 'Memory', 'Config', 'Settings']

export default function App() {
  const { projects, loading: projectsLoading, refresh: refreshProjects, newProjectIds } = useProjects()
  const [activeProject, setActiveProject] = useState(null)
  const [activeTab, setActiveTab] = useState('Tasks')
  const [showNewWizard, setShowNewWizard] = useState(false)
  const [planningBadge, setPlanningBadge] = useState(false)
  const { entries, runStatus, clear } = useAgentStream(activeProject)

  // Auto-select first project
  useEffect(() => {
    if (!activeProject && projects.length > 0) {
      setActiveProject(projects[0].id)
    }
  }, [projects, activeProject])

  const activeProjectData = projects.find(p => p.id === activeProject)

  const handleProjectSelect = (id) => {
    setActiveProject(id)
    setActiveTab('Tasks')
    setPlanningBadge(false)
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

  const handleProjectCreated = (projectId, projectPath) => {
    setShowNewWizard(false)
    refreshProjects()
    // Wait for project list to update, then switch to it
    setTimeout(() => {
      setActiveProject(projectId)
      setActiveTab('Planning')
    }, 500)
  }

  return (
    <>
      <header className="app-header">
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
          <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            {projects.length} project{projects.length !== 1 ? 's' : ''}
          </span>
          <button
            className="primary"
            style={{ fontSize: 12, padding: '5px 12px' }}
            onClick={() => setShowNewWizard(true)}
          >
            + New Project
          </button>
        </span>
      </header>

      <div className="app-body">
        <nav className="sidebar">
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
        </nav>

        <div className="main-content">
          {activeProject ? (
            <>
              {activeTab !== 'Planning' && activeTab !== 'Settings' && activeTab !== 'Config' && activeTab !== 'PM' && (
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
                    {tab}
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
              <div className="panel-body" style={activeTab === 'Planning' ? { padding: 0, overflow: 'hidden' } : {}}>
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
                {activeTab === 'Settings' && (
                  <Settings />
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

      {showNewWizard && (
        <NewProjectWizard
          onCreated={handleProjectCreated}
          onClose={() => setShowNewWizard(false)}
        />
      )}
    </>
  )
}
