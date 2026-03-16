import { useEffect, useRef } from 'react'

function formatMessage(type, data) {
  if (!data) return type
  switch (type) {
    case 'connected':
      return `connected to ${data.project_id || 'project'}`
    case 'session_start':
      return `🌸 Session started — ${data.pending || 0} tasks pending  ${data.project || ''}`
    case 'task_start':
      return `▶ ${data.task_id}: ${data.title || ''}${data.remaining !== undefined ? `  (${data.remaining} remaining)` : ''}`
    case 'task_progress':
      return `  iter ${data.iter || '?'}${data.thought_snippet ? ` — ${data.thought_snippet.slice(0, 100)}` : ''}`
    case 'task_complete':
      return `✓ ${data.task_id} done${data.result_snippet ? `\n  ${data.result_snippet.slice(0, 150)}` : ''}`
    case 'task_failed':
      return `✗ ${data.task_id} failed: ${(data.error || '').slice(0, 150)}`
    case 'task_blocked':
      return `⚠ ${data.task_id} blocked${data.waiting_on ? ` — waiting on: ${data.waiting_on.join(', ')}` : ''}`
    case 'session_complete':
      return `🎉 Session complete — ${(data.done || []).length} done, ${(data.failed || []).length} failed`
    case 'error':
      return `Error: ${data.message || ''}`
    default:
      return JSON.stringify(data)
  }
}

export default function AgentStream({ entries, onClear }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries.length])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>Live agent events</span>
        <button onClick={onClear} style={{ fontSize: 11, padding: '2px 8px' }}>Clear</button>
      </div>
      <div className="agent-stream">
        {entries.length === 0 ? (
          <div style={{ color: 'var(--text-dim)', padding: '20px 0', textAlign: 'center' }}>
            Waiting for events…
          </div>
        ) : (
          entries.map(entry => (
            <div key={entry.id} className={`stream-entry type-${entry.type}`}>
              <span className="stream-ts">{entry.ts}</span>
              <span className="stream-type">{entry.type}</span>
              <span className="stream-msg">{formatMessage(entry.type, entry.data)}</span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
