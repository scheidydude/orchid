import { useState, useEffect, useRef, useCallback } from 'react'

function useDiscussionWS(projectId, onMessage) {
  const wsRef = useRef(null)
  const reconnectRef = useRef(null)

  const connect = useCallback(() => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/${projectId}/discussion`)
    wsRef.current = ws
    ws.onmessage = e => {
      try { onMessage(JSON.parse(e.data)) } catch {}
    }
    ws.onclose = () => {
      reconnectRef.current = setTimeout(connect, 3000)
    }
    ws.onerror = () => ws.close()
    return ws
  }, [projectId, onMessage])

  useEffect(() => {
    const ws = connect()
    return () => {
      clearTimeout(reconnectRef.current)
      ws.close()
    }
  }, [connect])

  return wsRef
}

export default function DiscussionPanel({ projectId, onReadyToAdvance }) {
  const [turns, setTurns] = useState([])
  const [contextMd, setContextMd] = useState('')
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [providerOverride, setProviderOverride] = useState('')
  const [readyBanner, setReadyBanner] = useState(false)
  const [suggestions, setSuggestions] = useState([])
  const [error, setError] = useState(null)
  const [streamMsg, setStreamMsg] = useState('')
  const bottomRef = useRef(null)

  // Load history on mount
  useEffect(() => {
    fetch(`/api/projects/${projectId}/discussion`)
      .then(r => r.json())
      .then(d => {
        setTurns(d.turns || [])
        setContextMd(d.context_md || '')
      })
      .catch(() => {})
  }, [projectId])

  // Scroll to bottom on new message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, streamMsg])

  const handleWsMessage = useCallback((msg) => {
    if (msg.type === 'thinking') {
      setThinking(true)
      setStreamMsg('')
    } else if (msg.type === 'token') {
      setStreamMsg(msg.data || '')
    } else if (msg.type === 'done') {
      const agentMsg = streamMsg || ''
      setTurns(prev => [...prev, {
        role: 'agent',
        message: agentMsg,
        timestamp: new Date().toISOString(),
      }])
      setStreamMsg('')
      setThinking(false)
      if (msg.data?.ready_to_advance) {
        setReadyBanner(true)
      }
      setSuggestions(msg.data?.suggestions || [])
    } else if (msg.type === 'error') {
      setError(msg.data)
      setThinking(false)
      setStreamMsg('')
    }
  }, [streamMsg])

  const wsRef = useDiscussionWS(projectId, handleWsMessage)

  const sendMessage = () => {
    const text = input.trim()
    if (!text || thinking) return
    setInput('')
    setError(null)
    setReadyBanner(false)
    setSuggestions([])

    // Optimistic user message
    setTurns(prev => [...prev, {
      role: 'user',
      message: text,
      timestamp: new Date().toISOString(),
    }])

    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ message: text, provider_override: providerOverride || null }))
    } else {
      setError('WebSocket not connected — retrying…')
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const useSuggestion = (s) => {
    setInput(s)
  }

  return (
    <div className="discussion-panel">
      {contextMd && contextMd.trim() !== '' && (
        <details className="context-summary">
          <summary>Captured context</summary>
          <pre className="context-md">{contextMd}</pre>
        </details>
      )}

      <div className="discussion-messages">
        {turns.length === 0 && !thinking && (
          <div className="discussion-empty">
            Tell me what you want to build. I'll ask a few questions to understand your requirements.
          </div>
        )}

        {turns.map((t, i) => (
          <div key={i} className={`discussion-bubble ${t.role}`}>
            <div className="bubble-role">{t.role === 'user' ? 'You' : 'Orchid'}</div>
            <div className="bubble-text">{t.message}</div>
            {t.timestamp && (
              <div className="bubble-time">{new Date(t.timestamp).toLocaleTimeString()}</div>
            )}
          </div>
        ))}

        {thinking && (
          <div className="discussion-bubble agent">
            <div className="bubble-role">Orchid</div>
            <div className="bubble-text">
              {streamMsg || <span className="thinking-dots">thinking<span className="dots">…</span></span>}
            </div>
          </div>
        )}

        {error && (
          <div className="error-msg" style={{ margin: '8px 0' }}>{error}</div>
        )}

        <div ref={bottomRef} />
      </div>

      {suggestions.length > 0 && (
        <div className="suggestion-chips">
          {suggestions.slice(0, 3).map((s, i) => (
            <button key={i} className="suggestion-chip" onClick={() => useSuggestion(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      {readyBanner && (
        <div className="ready-banner">
          ✅ Requirements look complete!
          <button className="primary" onClick={onReadyToAdvance} style={{ marginLeft: 12 }}>
            Advance to Requirements →
          </button>
        </div>
      )}

      <div className="discussion-input-row">
        <select
          className="provider-select"
          value={providerOverride}
          onChange={e => setProviderOverride(e.target.value)}
          title="Provider override"
        >
          <option value="">auto</option>
          <option value="claude">claude</option>
          <option value="local">local</option>
          <option value="ollama">ollama</option>
        </select>
        <textarea
          className="discussion-input"
          placeholder="Describe what you want to build… (Enter to send, Shift+Enter for newline)"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={2}
          disabled={thinking}
        />
        <button
          className="primary"
          onClick={sendMessage}
          disabled={thinking || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  )
}
