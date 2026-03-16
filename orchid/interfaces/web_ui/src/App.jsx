import { useState, useEffect } from 'react'
import ProjectSwitcher from './components/ProjectSwitcher.jsx'
import TaskBoard from './components/TaskBoard.jsx'
import AgentStream from './components/AgentStream.jsx'
import RunControls from './components/RunControls.jsx'
import DecisionLog from './components/DecisionLog.jsx'
import SessionHistory from './components/SessionHistory.jsx'
import RecallSearch from './components/RecallSearch.jsx'
import HotMemory from './components/HotMemory.jsx'
import { useProjects } from './hooks/useProjects.js'
import { useAgentStream } from './hooks/useAgentStream.js'

const TABS = ['Tasks', 'Stream', 'Decisions', 'Sessions', 'Recall', 'Memory']

export default function App() {
  const { projects, loading: projectsLoading, refresh: refreshProjects } = useProjects()
  const [activeProject, setActiveProject] = useState(null)
  const [activeTab, setActiveTab] = useState('Tasks')
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
  }

  const handleRunChange = () => {
    refreshProjects()
  }

  return (
    <>
      <header className="app-header">
        <span className="logo">🌸 Orchid</span>
        {activeProjectData && (
          <>
            <span style={{ color: 'var(--text-dim)', fontSize: 13 }}>
              {activeProjectData.name}
            </span>
            {activeProjectData.running && (
              <span style={{ fontSize: 12, color: '#56d364' }}>
                <span className="project-running-dot" />running
              </span>
            )}
          </>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-dim)' }}>
          {projects.length} project{projects.length !== 1 ? 's' : ''}
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
            />
          )}
        </nav>

        <div className="main-content">
          {activeProject ? (
            <>
              <RunControls
                projectId={activeProject}
                runStatus={runStatus}
                onRunChange={handleRunChange}
              />
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
                  </button>
                ))}
              </div>
              <div className="panel-body">
                {activeTab === 'Tasks' && (
                  <TaskBoard projectId={activeProject} runStatus={runStatus} />
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
              </div>
            </>
          ) : (
            <div className="empty-state" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {projectsLoading ? 'Loading projects…' : 'Select a project from the sidebar'}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
