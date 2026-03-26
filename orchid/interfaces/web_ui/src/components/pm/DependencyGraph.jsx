import { useState, useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'

const STATUS_COLOR = {
  DONE: '#56d364',
  BLOCKED: '#f85149',
  IN_PROGRESS: '#e3b341',
  TODO: '#8b949e',
  SKIPPED: '#388bfd',
  CANCELLED: '#6e7681',
}

function buildElements(tasks) {
  const nodeIds = new Set(tasks.map(t => t.id))

  const nodes = tasks.map(t => ({
    data: {
      id: t.id,
      label: `${t.id}\n${t.title.length > 20 ? t.title.slice(0, 20) + '…' : t.title}`,
      status: t.status,
      title: t.title,
      color: STATUS_COLOR[t.status] || '#8b949e',
    },
  }))

  const edges = []
  for (const t of tasks) {
    for (const dep of (t.depends_on || [])) {
      // Only add edge if both endpoints exist in the current task list
      if (nodeIds.has(dep)) {
        edges.push({ data: { source: dep, target: t.id, id: `${dep}->${t.id}` } })
      }
    }
  }
  return [...nodes, ...edges]
}

function findCriticalPath(tasks) {
  // Longest dependency chain by node count
  const taskMap = Object.fromEntries(tasks.map(t => [t.id, t]))
  const memo = {}

  function depth(id) {
    if (id in memo) return memo[id]
    const task = taskMap[id]
    if (!task || !task.depends_on?.length) { memo[id] = [id]; return [id] }
    let longest = []
    for (const dep of task.depends_on) {
      const chain = depth(dep)
      if (chain.length > longest.length) longest = chain
    }
    memo[id] = [...longest, id]
    return memo[id]
  }

  let critical = []
  for (const t of tasks) {
    const chain = depth(t.id)
    if (chain.length > critical.length) critical = chain
  }
  return new Set(critical)
}

export default function DependencyGraph({ projectId }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tooltip, setTooltip] = useState(null)
  const containerRef = useRef(null)
  const cyRef = useRef(null)

  useEffect(() => {
    if (!projectId) return
    setLoading(true)
    fetch(`/api/projects/${projectId}/tasks`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(data => { setTasks(data); setError(null) })
      .catch(err => setError(String(err)))
      .finally(() => setLoading(false))
  }, [projectId])

  useEffect(() => {
    if (loading || !containerRef.current || tasks.length === 0) return

    const elements = buildElements(tasks)
    const critical = findCriticalPath(tasks)

    if (cyRef.current) cyRef.current.destroy()

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': 'data(color)',
            'label': 'data(label)',
            'color': '#fff',
            'font-size': '10px',
            'text-wrap': 'wrap',
            'text-max-width': '80px',
            'text-valign': 'center',
            'text-halign': 'center',
            'width': 80,
            'height': 40,
            'shape': 'round-rectangle',
            'border-width': 0,
          },
        },
        {
          selector: 'node.critical',
          style: {
            'border-width': 3,
            'border-color': '#f0883e',
          },
        },
        {
          selector: 'edge',
          style: {
            'width': 1.5,
            'line-color': '#444c56',
            'target-arrow-color': '#444c56',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
          },
        },
        {
          selector: 'edge.critical',
          style: {
            'line-color': '#f0883e',
            'target-arrow-color': '#f0883e',
            'width': 2.5,
          },
        },
      ],
      layout: {
        name: 'breadthfirst',
        directed: true,
        padding: 16,
        spacingFactor: 1.2,
      },
    })

    // Mark critical path
    critical.forEach(id => {
      cy.getElementById(id).addClass('critical')
    })
    // Mark critical edges
    cy.edges().forEach(edge => {
      if (critical.has(edge.data('source')) && critical.has(edge.data('target'))) {
        edge.addClass('critical')
      }
    })

    // Node click → zoom in + show tooltip
    cy.on('tap', 'node', (evt) => {
      const node = evt.target
      setTooltip({ id: node.id(), title: node.data('title'), status: node.data('status') })
      cy.animate({
        zoom: 2,
        center: { eles: node },
      }, { duration: 300 })
    })
    cy.on('tap', (evt) => {
      if (evt.target === cy) setTooltip(null)
    })

    cyRef.current = cy
    return () => { if (cyRef.current) { cyRef.current.destroy(); cyRef.current = null } }
  }, [tasks, loading])

  if (loading) return <div className="loading" style={{ padding: 12 }}>Loading graph…</div>
  if (error) return <div className="error-msg">Error: {error}</div>
  if (tasks.length === 0) return <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: 12 }}>No tasks to graph.</div>

  const hasDeps = tasks.some(t => t.depends_on?.length > 0)

  return (
    <div style={{ position: 'relative' }}>
      {!hasDeps && (
        <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 8, fontStyle: 'italic' }}>
          No dependencies defined — tasks will appear as isolated nodes.
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 6, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {Object.entries(STATUS_COLOR).map(([s, c]) => (
          <span key={s} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: c, display: 'inline-block' }} />
            {s}
          </span>
        ))}
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: 'transparent', border: '2px solid #f0883e', display: 'inline-block' }} />
          Critical path
        </span>
      </div>
      <div style={{ position: 'relative' }}>
        <div ref={containerRef} style={{ width: '100%', height: 380, background: 'var(--bg)', borderRadius: 6, border: '1px solid var(--border)' }} />
        <button
          onClick={() => cyRef.current?.fit(undefined, 16)}
          title="Reset view"
          style={{
            position: 'absolute', top: 8, right: 8,
            padding: '4px 10px', fontSize: 11,
            background: '#21262d', border: '1px solid #444c56',
            borderRadius: 5, color: '#c9d1d9', cursor: 'pointer',
          }}
        >
          ⤢ Reset
        </button>
        {tooltip && (
          <div style={{
            position: 'absolute', bottom: 8, left: 8,
            background: '#1c2128',
            border: '1px solid #444c56',
            borderRadius: 6, padding: '10px 14px', fontSize: 12, maxWidth: 300,
            boxShadow: '0 4px 16px rgba(0,0,0,0.7)',
            zIndex: 10,
          }}>
            <div style={{ fontWeight: 700, marginBottom: 4, color: '#e6edf3' }}>{tooltip.id}</div>
            <div style={{ marginBottom: 6, color: '#c9d1d9', lineHeight: 1.4 }}>{tooltip.title}</div>
            <div style={{
              display: 'inline-block', fontSize: 11, padding: '2px 8px', borderRadius: 10,
              background: (STATUS_COLOR[tooltip.status] || '#8b949e') + '33',
              color: STATUS_COLOR[tooltip.status] || '#8b949e',
              border: `1px solid ${STATUS_COLOR[tooltip.status] || '#8b949e'}`,
            }}>
              {tooltip.status}
            </div>
            <button
              onClick={() => setTooltip(null)}
              style={{
                position: 'absolute', top: 6, right: 8,
                background: 'none', border: 'none', cursor: 'pointer',
                color: '#8b949e', fontSize: 14, lineHeight: 1, padding: 2,
              }}
            >✕</button>
          </div>
        )}
      </div>
    </div>
  )
}
