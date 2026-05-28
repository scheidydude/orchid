import { useState, useEffect } from 'react'

function schemaToTemplate(parameters) {
  const props = parameters?.properties
  if (!props) return {}
  const out = {}
  for (const [k, v] of Object.entries(props)) {
    switch (v.type) {
      case 'string':  out[k] = ''; break
      case 'integer':
      case 'number':  out[k] = 0; break
      case 'boolean': out[k] = false; break
      case 'array':   out[k] = []; break
      case 'object':  out[k] = {}; break
      default:        out[k] = null
    }
  }
  return out
}

function RequiredBadge({ required, name }) {
  if (!required?.includes(name)) return null
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, color: 'var(--accent)',
      background: 'color-mix(in srgb, var(--accent) 15%, transparent)',
      borderRadius: 3, padding: '1px 5px', marginLeft: 4,
    }}>required</span>
  )
}

// ── Single-server / single-tool picker (mcp_tool) ────────────────────────────

function McpToolMode({ servers, onApply }) {
  const [selServer, setSelServer] = useState(null)
  const [selTool, setSelTool]     = useState(null)

  const serverObj  = servers.find(s => s.server === selServer)
  const tools      = serverObj?.tools || []
  const serverErr  = serverObj?.error

  const template = selTool ? schemaToTemplate(selTool.parameters) : null
  const props    = selTool?.parameters?.properties || {}
  const required = selTool?.parameters?.required   || []

  const handleApply = () => {
    if (!selServer || !selTool) return
    onApply({ server: selServer, tool: selTool.name, args: template ?? {} })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, flex: 1, minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>

        {/* Server list */}
        <div style={{
          width: 180, flexShrink: 0,
          border: '1px solid var(--border)', borderRadius: 6,
          overflowY: 'auto',
        }}>
          <div style={{ padding: '6px 10px', fontSize: 11, color: 'var(--text-mute)', fontWeight: 600, letterSpacing: '0.5px', borderBottom: '1px solid var(--border)' }}>
            SERVERS
          </div>
          {servers.length === 0 && (
            <div style={{ padding: '12px 10px', fontSize: 12, color: 'var(--text-mute)' }}>No servers found</div>
          )}
          {servers.map(s => (
            <button
              key={s.server}
              onClick={() => { setSelServer(s.server); setSelTool(null) }}
              style={{
                width: '100%', textAlign: 'left', padding: '8px 10px',
                background: selServer === s.server ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'none',
                borderLeft: selServer === s.server ? '3px solid var(--accent)' : '3px solid transparent',
                border: 'none', borderBottom: '1px solid var(--border)',
                fontSize: 12, color: selServer === s.server ? 'var(--accent)' : 'var(--text)',
                cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 2,
              }}
            >
              <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.server}</span>
              {s.error
                ? <span style={{ fontSize: 10, color: 'var(--error-fg)' }}>⚠ error</span>
                : <span style={{ fontSize: 10, color: 'var(--text-mute)' }}>{s.tools.length} tool{s.tools.length !== 1 ? 's' : ''}</span>
              }
            </button>
          ))}
        </div>

        {/* Tool list */}
        <div style={{
          flex: 1, minWidth: 0,
          border: '1px solid var(--border)', borderRadius: 6,
          overflowY: 'auto',
        }}>
          <div style={{ padding: '6px 10px', fontSize: 11, color: 'var(--text-mute)', fontWeight: 600, letterSpacing: '0.5px', borderBottom: '1px solid var(--border)' }}>
            TOOLS {selServer ? `— ${selServer}` : ''}
          </div>
          {!selServer && (
            <div style={{ padding: '20px 12px', fontSize: 12, color: 'var(--text-mute)' }}>← Select a server</div>
          )}
          {selServer && serverErr && (
            <div style={{ padding: '12px', fontSize: 12, color: 'var(--error-fg)' }}>⚠ {serverErr}</div>
          )}
          {selServer && !serverErr && tools.length === 0 && (
            <div style={{ padding: '12px', fontSize: 12, color: 'var(--text-mute)' }}>No tools</div>
          )}
          {tools.map(t => (
            <button
              key={t.name}
              onClick={() => setSelTool(t)}
              style={{
                width: '100%', textAlign: 'left', padding: '8px 12px',
                background: selTool?.name === t.name ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'none',
                borderLeft: selTool?.name === t.name ? '3px solid var(--accent)' : '3px solid transparent',
                border: 'none', borderBottom: '1px solid var(--border)',
                fontSize: 12, color: selTool?.name === t.name ? 'var(--accent)' : 'var(--text)',
                cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 2,
              }}
            >
              <span style={{ fontWeight: 600, fontFamily: 'var(--mono)' }}>{t.name}</span>
              {t.description && (
                <span style={{ fontSize: 11, color: 'var(--text-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {t.description}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Tool detail / args preview */}
      {selTool && (
        <div style={{
          border: '1px solid var(--border)', borderRadius: 6, padding: '12px 14px',
          background: 'var(--bg)', fontSize: 12,
        }}>
          <div style={{ fontWeight: 600, marginBottom: 4, fontFamily: 'var(--mono)' }}>{selTool.name}</div>
          {selTool.description && (
            <div style={{ color: 'var(--text-dim)', marginBottom: 8 }}>{selTool.description}</div>
          )}
          {Object.keys(props).length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ fontSize: 11, color: 'var(--text-mute)', fontWeight: 600, letterSpacing: '0.4px', marginBottom: 2 }}>ARGUMENTS</div>
              {Object.entries(props).map(([k, v]) => (
                <div key={k} style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                  <code style={{ color: 'var(--accent-2)', minWidth: 120 }}>{k}</code>
                  <span style={{ color: 'var(--text-mute)', fontSize: 11 }}>{v.type}</span>
                  <RequiredBadge required={required} name={k} />
                  {v.description && <span style={{ color: 'var(--text-dim)', fontSize: 11 }}>— {v.description}</span>}
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-mute)', fontSize: 11 }}>No arguments</div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <button className="primary" disabled={!selServer || !selTool} onClick={handleApply}>
          Use {selTool ? `"${selTool.name}"` : 'tool'}
        </button>
      </div>
    </div>
  )
}

// ── Multi-server picker (agent_tool) ─────────────────────────────────────────

function AgentToolMode({ servers, currentConfig, onApply }) {
  const [selected, setSelected] = useState(new Set(currentConfig?.servers || []))

  const toggle = (name) =>
    setSelected(prev => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })

  const handleApply = () => {
    onApply({ ...currentConfig, servers: [...selected] })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, flex: 1, minHeight: 0 }}>
      <div style={{
        flex: 1, overflowY: 'auto',
        border: '1px solid var(--border)', borderRadius: 6,
      }}>
        <div style={{ padding: '6px 10px', fontSize: 11, color: 'var(--text-mute)', fontWeight: 600, letterSpacing: '0.5px', borderBottom: '1px solid var(--border)' }}>
          SELECT SERVERS TO INCLUDE
        </div>
        {servers.length === 0 && (
          <div style={{ padding: '20px', fontSize: 12, color: 'var(--text-mute)' }}>No servers found</div>
        )}
        {servers.map(s => (
          <label
            key={s.server}
            style={{
              display: 'flex', alignItems: 'flex-start', gap: 10,
              padding: '10px 14px', cursor: 'pointer', fontSize: 13,
              borderBottom: '1px solid var(--border)',
              background: selected.has(s.server) ? 'color-mix(in srgb, var(--accent) 8%, transparent)' : 'none',
            }}
          >
            <input
              type="checkbox"
              checked={selected.has(s.server)}
              onChange={() => toggle(s.server)}
              style={{ width: 'auto', marginTop: 2, accentColor: 'var(--accent)', cursor: 'pointer' }}
            />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>{s.server}</div>
              {s.error
                ? <div style={{ fontSize: 11, color: 'var(--error-fg)', marginTop: 2 }}>⚠ {s.error}</div>
                : (
                  <div style={{ fontSize: 11, color: 'var(--text-mute)', marginTop: 2 }}>
                    {s.tools.map(t => t.name).join(', ') || 'No tools'}
                  </div>
                )
              }
            </div>
          </label>
        ))}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
          {selected.size} server{selected.size !== 1 ? 's' : ''} selected
        </span>
        <button className="primary" onClick={handleApply}>
          Apply
        </button>
      </div>
    </div>
  )
}

// ── Root picker ───────────────────────────────────────────────────────────────

export default function MCPToolPicker({ taskType, currentConfig, onApply, onClose }) {
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState(null)
  const [servers, setServers] = useState([])

  useEffect(() => {
    fetch('/api/scheduler/mcp-tools')
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => setServers(d.servers || []))
      .catch(e => setFetchError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const handleApply = (config) => { onApply(config); onClose() }

  return (
    <div
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.6)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1200,
      }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 20,
        width: taskType === 'agent_tool' ? 480 : 740,
        maxWidth: '95vw',
        maxHeight: '85vh',
        display: 'flex', flexDirection: 'column', gap: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <h4 style={{ margin: 0 }}>
            {taskType === 'agent_tool' ? '🔌 Select MCP Servers' : '🔌 Browse MCP Tools'}
          </h4>
          <button className="ghost icon" onClick={onClose}>✕</button>
        </div>

        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-dim)', padding: '20px 0' }}>
            <span className="spinner" />
            Probing MCP servers… (may take a moment)
          </div>
        )}

        {!loading && fetchError && (
          <div style={{ color: 'var(--error-fg)', fontSize: 13 }}>⚠ {fetchError}</div>
        )}

        {!loading && !fetchError && taskType === 'mcp_tool' && (
          <McpToolMode servers={servers} onApply={handleApply} />
        )}

        {!loading && !fetchError && taskType === 'agent_tool' && (
          <AgentToolMode servers={servers} currentConfig={currentConfig} onApply={handleApply} />
        )}

        <button className="ghost" style={{ alignSelf: 'flex-start' }} onClick={onClose}>Cancel</button>
      </div>
    </div>
  )
}
