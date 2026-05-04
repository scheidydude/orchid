import { useState, useEffect, useRef, useCallback, useMemo } from 'react'

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

export default function DiscussionPanel({ projectId, onReadyToAdvance, onReset, advancing = false, advanceLog = [] }) {
  const [turns, setTurns] = useState([])
  const [contextMd, setContextMd] = useState('')
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [providerOverride, setProviderOverride] = useState('local')
  const [readyBanner, setReadyBanner] = useState(false)
  const [suggestions, setSuggestions] = useState([])
  const [error, setError] = useState(null)
  const [streamMsg, setStreamMsg] = useState('')
  const [resetting, setResetting] = useState(false)
  const streamMsgRef = useRef('')  // ref copy avoids stale closure in WS callback
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

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
      streamMsgRef.current = ''
    } else if (msg.type === 'token') {
      streamMsgRef.current = msg.data || ''
      setStreamMsg(msg.data || '')
    } else if (msg.type === 'done') {
      const agentMsg = streamMsgRef.current || ''
      setTurns(prev => [...prev, {
        role: 'agent',
        message: agentMsg,
        timestamp: new Date().toISOString(),
      }])
      streamMsgRef.current = ''
      setStreamMsg('')
      setThinking(false)
      setTimeout(() => inputRef.current?.focus(), 0)
      if (msg.data?.ready_to_advance) {
        setReadyBanner(true)
      }
      setSuggestions(msg.data?.suggestions || [])
    } else if (msg.type === 'error') {
      setError(msg.data)
      setThinking(false)
      streamMsgRef.current = ''
      setStreamMsg('')
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [])  // stable — uses streamMsgRef to avoid stale closure and WS reconnection

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
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const resetDiscussion = async () => {
    if (!window.confirm('Reset discussion and start over? This clears the conversation history.')) return
    setResetting(true)
    try {
      const res = await fetch(`/api/projects/${projectId}/discussion`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setTurns([])
      setContextMd('')
      setReadyBanner(false)
      setSuggestions([])
      setError(null)
      streamMsgRef.current = ''
      setStreamMsg('')
      onReset?.()
    } catch (err) {
      setError(`Reset failed: ${err.message}`)
    } finally {
      setResetting(false)
    }
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

        {advancing && advanceLog.length > 0 && (
          <div className="discussion-bubble agent">
            <div className="bubble-role">Orchid</div>
            <div className="bubble-text">
              {advanceLog.map((l, i) => (
                <div key={i}>{l}</div>
              ))}
              <span className="thinking-dots">working<span className="dots">…</span></span>
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
            Generate Requirements →
          </button>
        </div>
      )}

      {!readyBanner && turns.length >= 1 && !advancing && (
        <div className="ready-banner" style={{ background: 'var(--surface-2, #2a2a2a)', border: '1px solid var(--border)' }}>
          Ready to proceed?
          <button className="primary" onClick={onReadyToAdvance} style={{ marginLeft: 12 }}>
            Generate Requirements →
          </button>
        </div>
      )}

      <div className="discussion-input-row" style={{ position: 'relative' }}>
        {turns.length > 0 && (
          <button
            title="Reset discussion"
            onClick={resetDiscussion}
            disabled={resetting || thinking || advancing}
            style={{ background: 'none', border: 'none', color: 'var(--text-dim)', cursor: 'pointer', fontSize: 14, padding: '0 4px', flexShrink: 0 }}
          >
            ↺
          </button>
        )}
        <select
          className="provider-select"
          value={providerOverride}
          onChange={e => setProviderOverride(e.target.value)}
          title="Provider override"
        >
          <option value="local">local</option>
          <option value="claude">claude</option>
          <option value="ollama">ollama</option>
          <option value="">auto</option>
        </select>
        <textarea
          ref={inputRef}
          className="discussion-input"
          placeholder={advancing ? 'Working…' : 'Describe what you want to build… (Enter to send, Shift+Enter for newline)'}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={2}
          disabled={thinking || advancing}
        />
        <button
          className="primary"
          onClick={sendMessage}
          disabled={thinking || advancing || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  )
}
