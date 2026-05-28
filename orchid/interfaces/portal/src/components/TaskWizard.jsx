import { useState, useEffect, useRef } from 'react'

const OPENING = "What would you like this scheduled task to do? Describe your goal in plain language — for example, \"send me a daily summary of my emails\" or \"check a website for changes every hour\" — and I'll help you configure it."

function Message({ msg }) {
  const isUser = msg.role === 'user'
  return (
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      <div style={{
        maxWidth: '82%',
        padding: '10px 14px',
        borderRadius: isUser ? '14px 14px 3px 14px' : '14px 14px 14px 3px',
        background: isUser
          ? 'var(--accent)'
          : msg.error
            ? 'color-mix(in srgb, var(--error-fg) 12%, var(--surface2))'
            : 'var(--surface2)',
        color: isUser ? '#fff' : 'var(--text)',
        fontSize: 13,
        lineHeight: 1.55,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}>
        {msg.content}
      </div>
    </div>
  )
}

export default function TaskWizard({ onApply, onClose }) {
  const [messages, setMessages]     = useState([{ role: 'assistant', content: OPENING }])
  const [input, setInput]           = useState('')
  const [loading, setLoading]       = useState(false)
  const [taskConfig, setTaskConfig] = useState(null)
  const [mcpServers, setMcpServers] = useState([])
  const [mcpReady, setMcpReady]     = useState(false)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  // Fetch MCP servers so LLM knows what's available
  useEffect(() => {
    fetch('/api/scheduler/mcp-tools')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setMcpServers(d.servers || []) })
      .catch(() => {})
      .finally(() => setMcpReady(true))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Escape closes (but not while CronBuilder/MCPPicker layers might be open)
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose])

  const send = async () => {
    const text = input.trim()
    if (!text || loading) return

    const userMsg = { role: 'user', content: text }
    // newMessages includes the hardcoded opening + all prior + new user msg
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    setInput('')
    setLoading(true)

    try {
      // API only sees the real conversation (skip hardcoded opening message)
      const apiMessages = newMessages
        .slice(1)
        .map(m => ({ role: m.role, content: m.content }))

      const r = await fetch('/api/scheduler/wizard', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: apiMessages,
          mcp_servers: mcpServers,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        }),
      })

      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `HTTP ${r.status}`)
      }

      const data = await r.json()

      if (data.message) {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message }])
      }
      if (data.task_config) {
        setTaskConfig(data.task_config)
      }
    } catch (e) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `⚠ ${e.message}. Try rephrasing or switch to the manual form.`,
        error: true,
      }])
    } finally {
      setLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  return (
    <div
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.72)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1300,
      }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 10,
        width: 640, maxWidth: '95vw',
        maxHeight: '88vh',
        display: 'flex', flexDirection: 'column',
        boxShadow: 'var(--shadow)',
      }}>

        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div style={{
          padding: '14px 18px',
          borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          flexShrink: 0,
        }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>✨ Task Wizard</div>
            <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 1 }}>
              Describe what you want — I'll fill in the form
            </div>
          </div>
          <button className="ghost icon" onClick={onClose}>✕</button>
        </div>

        {/* ── Messages ───────────────────────────────────────────────────── */}
        <div style={{
          flex: 1, overflowY: 'auto',
          padding: '16px 18px',
          display: 'flex', flexDirection: 'column', gap: 10,
        }}>
          {messages.map((msg, i) => <Message key={i} msg={msg} />)}

          {loading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-dim)', fontSize: 13, paddingLeft: 4 }}>
              <span className="spinner" style={{ width: 13, height: 13, borderWidth: 2 }} />
              Thinking…
            </div>
          )}

          {taskConfig && !loading && (
            <div style={{
              background: 'color-mix(in srgb, var(--success-fg) 10%, var(--surface2))',
              border: '1px solid color-mix(in srgb, var(--success-fg) 25%, transparent)',
              borderRadius: 8, padding: '12px 14px',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
              flexShrink: 0,
            }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--success-fg)' }}>
                  ✓ {taskConfig.name}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 3 }}>
                  {taskConfig.task_type} · <code style={{ fontFamily: 'var(--mono)' }}>{taskConfig.schedule}</code>
                </div>
              </div>
              <button
                className="primary"
                onClick={() => onApply(taskConfig)}
                style={{ flexShrink: 0 }}
              >
                Apply to form →
              </button>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* ── Input ──────────────────────────────────────────────────────── */}
        <div style={{
          padding: '10px 14px 6px',
          borderTop: '1px solid var(--border)',
          display: 'flex', gap: 8, alignItems: 'flex-end',
          flexShrink: 0,
        }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Type your response… (Enter to send, Shift+Enter for new line)"
            rows={2}
            disabled={loading}
            style={{
              flex: 1, resize: 'none',
              fontFamily: 'inherit', fontSize: 13,
              padding: '8px 11px', borderRadius: 6,
              border: '1px solid var(--border)',
              background: 'var(--bg)', color: 'var(--text)',
            }}
          />
          <button
            className="primary"
            onClick={send}
            disabled={loading || !input.trim()}
            style={{ padding: '8px 16px', alignSelf: 'flex-end', flexShrink: 0 }}
          >
            Send
          </button>
        </div>

        {/* ── Footer ─────────────────────────────────────────────────────── */}
        <div style={{
          padding: '5px 14px 12px',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 11, color: 'var(--text-mute)' }}>
            {mcpReady
              ? `${mcpServers.length} MCP server${mcpServers.length !== 1 ? 's' : ''} available`
              : 'Loading MCP servers…'}
          </span>
          <button
            className="ghost"
            style={{ fontSize: 12, color: 'var(--text-dim)' }}
            onClick={onClose}
          >
            Use manual form instead
          </button>
        </div>
      </div>
    </div>
  )
}
