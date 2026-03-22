import { useState, useEffect, useRef, useCallback } from 'react';

// ── inject keyframe animations once ──────────────────────────────────────────
if (typeof document !== 'undefined' && !document.getElementById('orchid-dp-style')) {
  const s = document.createElement('style');
  s.id = 'orchid-dp-style';
  s.textContent = `
    @keyframes _orchid_spin {
      to { transform: rotate(360deg); }
    }
    @keyframes _orchid_fadein {
      from { opacity: 0; transform: translateY(-6px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    ._orchid_spinner {
      width: 16px; height: 16px;
      border: 2px solid #334155;
      border-top-color: #60a5fa;
      border-radius: 50%;
      animation: _orchid_spin 0.7s linear infinite;
      flex-shrink: 0;
      display: inline-block;
    }
    ._orchid_banner {
      animation: _orchid_fadein 0.35s ease;
    }
  `;
  document.head.appendChild(s);
}

// ── sub-components ────────────────────────────────────────────────────────────

function Spinner() {
  return <span className="_orchid_spinner" aria-label="loading" />;
}

/** Animated progress bar (0-100). */
function ProgressBar({ value }) {
  return (
    <div style={{
      width: '100%',
      height: '4px',
      background: '#1e293b',
      borderRadius: '2px',
      overflow: 'hidden',
    }}>
      <div style={{
        height: '100%',
        width: `${value}%`,
        background: 'linear-gradient(90deg, #2563eb, #60a5fa)',
        borderRadius: '2px',
        transition: 'width 0.4s ease',
      }} />
    </div>
  );
}

/** Status log shown while artifacts are being generated. */
function StatusLog({ lines }) {
  const endRef = useRef(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [lines]);

  if (!lines.length) return null;
  return (
    <div style={{
      alignSelf: 'flex-start',
      maxWidth: '90%',
      background: '#0f172a',
      border: '1px solid #1e3a5f',
      borderRadius: '0.75rem',
      padding: '0.625rem 0.875rem',
      fontSize: '0.8rem',
      fontFamily: 'monospace',
      color: '#94a3b8',
      display: 'flex',
      flexDirection: 'column',
      gap: '0.25rem',
    }}>
      {lines.map((line, i) => (
        <div key={i} style={{ color: line.startsWith('✓') ? '#4ade80' : '#94a3b8' }}>
          {line}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}

/** Success banner shown when artifacts are ready. */
function SuccessBanner({ onDismiss }) {
  return (
    <div className="_orchid_banner" style={{
      margin: '0.5rem 1rem',
      padding: '0.75rem 1rem',
      background: 'linear-gradient(135deg, #052e16, #14532d)',
      border: '1px solid #16a34a',
      borderRadius: '0.75rem',
      display: 'flex',
      alignItems: 'center',
      gap: '0.75rem',
    }}>
      <span style={{ fontSize: '1.25rem' }}>✅</span>
      <div style={{ flex: 1 }}>
        <div style={{ color: '#4ade80', fontWeight: 600, fontSize: '0.875rem' }}>
          Artifacts ready!
        </div>
        <div style={{ color: '#86efac', fontSize: '0.8rem', marginTop: '0.125rem' }}>
          REQUIREMENTS.md, ARCHITECTURE.md and tasks.md have been generated.
          Switch to the Artifacts tab to view them.
        </div>
      </div>
      <button
        onClick={onDismiss}
        style={{
          background: 'none',
          border: 'none',
          color: '#4ade80',
          cursor: 'pointer',
          fontSize: '1rem',
          padding: '0.25rem',
          lineHeight: 1,
        }}
        aria-label="Dismiss"
      >
        ✕
      </button>
    </div>
  );
}

// ── main component ────────────────────────────────────────────────────────────

export default function DiscussionPanel({ project }) {
  const [messages, setMessages]         = useState([]);
  const [input, setInput]               = useState('');
  const [connected, setConnected]       = useState(false);
  const [agentRunning, setAgentRunning] = useState(false);
  const [generatingArtifacts, setGeneratingArtifacts] = useState(false);
  const [statusLines, setStatusLines]   = useState([]);
  const [progress, setProgress]         = useState(0);
  const [showBanner, setShowBanner]     = useState(false);

  const wsRef          = useRef(null);
  const messagesEndRef = useRef(null);
  const inputRef       = useRef(null);

  // ── WebSocket lifecycle ───────────────────────────────────────────────────
  useEffect(() => {
    if (!project) return;

    const ws = new WebSocket(`ws://localhost:7842/ws/discussion/${project.id}`);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      switch (data.type) {
        // ── conversation history on connect ──────────────────────────────
        case 'history':
          setMessages(data.messages);
          break;

        // ── regular chat message (also signals agent done) ───────────────
        case 'message':
          setMessages(prev => [...prev, { role: data.role, content: data.content }]);
          setAgentRunning(false);
          setGeneratingArtifacts(false);
          // Re-focus input after agent responds
          setTimeout(() => inputRef.current?.focus(), 50);
          break;

        // ── agent started thinking / working ─────────────────────────────
        case 'agent_start':
          setAgentRunning(true);
          break;

        // ── human-readable status line during artifact generation ─────────
        case 'status':
          setStatusLines(prev => [...prev, data.message]);
          setGeneratingArtifacts(true);
          break;

        // ── progress percentage (0-100) ───────────────────────────────────
        case 'progress':
          setProgress(Number(data.message));
          break;

        // ── all artifacts written ─────────────────────────────────────────
        case 'artifacts_ready':
          setAgentRunning(false);
          setGeneratingArtifacts(false);
          setShowBanner(true);
          setTimeout(() => inputRef.current?.focus(), 50);
          break;

        // ── error during generation ───────────────────────────────────────
        case 'error':
          setStatusLines(prev => [...prev, `Error: ${data.message}`]);
          setAgentRunning(false);
          setGeneratingArtifacts(false);
          setTimeout(() => inputRef.current?.focus(), 50);
          break;

        default:
          break;
      }
    };

    ws.onclose = () => {
      setConnected(false);
      setAgentRunning(false);
      setGeneratingArtifacts(false);
    };
    ws.onerror = () => {
      setConnected(false);
      setAgentRunning(false);
      setGeneratingArtifacts(false);
    };

    return () => ws.close();
  }, [project?.id]);

  // ── auto-scroll to bottom ─────────────────────────────────────────────────
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, statusLines, agentRunning]);

  // ── send message ──────────────────────────────────────────────────────────
  const sendMessage = useCallback(() => {
    if (!input.trim() || !wsRef.current || agentRunning) return;
    const msg = input.trim();
    setInput('');
    setStatusLines([]);   // clear previous status log
    setProgress(0);
    setShowBanner(false);
    setAgentRunning(true);
    wsRef.current.send(JSON.stringify({ type: 'message', content: msg }));
  }, [input, agentRunning]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // ── placeholder text ──────────────────────────────────────────────────────
  const inputPlaceholder = generatingArtifacts
    ? 'Working… please wait'
    : agentRunning
      ? 'Agent is thinking…'
      : 'Type a message… (Enter to send, type "done" to generate artifacts)';

  // ── render ────────────────────────────────────────────────────────────────
  if (!project) {
    return (
      <div style={{ padding: '2rem', color: '#888' }}>
        Select a project to start discussion.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

      {/* ── header ── */}
      <div style={{
        padding: '0.75rem 1rem',
        borderBottom: '1px solid #333',
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: 600 }}>Discussion</span>
        <span style={{ fontSize: '0.75rem', color: connected ? '#4ade80' : '#f87171' }}>
          {connected ? '● Connected' : '○ Disconnected'}
        </span>
        {agentRunning && (
          <span style={{
            marginLeft: 'auto',
            display: 'flex',
            alignItems: 'center',
            gap: '0.375rem',
            fontSize: '0.75rem',
            color: '#60a5fa',
          }}>
            <Spinner />
            {generatingArtifacts ? 'Generating artifacts…' : 'Working…'}
          </span>
        )}
      </div>

      {/* ── progress bar (only during artifact generation) ── */}
      {generatingArtifacts && (
        <div style={{ padding: '0 1rem', paddingTop: '0.5rem', flexShrink: 0 }}>
          <ProgressBar value={progress} />
          <div style={{
            fontSize: '0.7rem',
            color: '#60a5fa',
            marginTop: '0.25rem',
            textAlign: 'right',
          }}>
            {progress}%
          </div>
        </div>
      )}

      {/* ── success banner ── */}
      {showBanner && (
        <SuccessBanner onDismiss={() => setShowBanner(false)} />
      )}

      {/* ── message list ── */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
      }}>
        {messages.map((msg, i) => (
          <div key={i} style={{
            alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
            maxWidth: '80%',
            background: msg.role === 'user' ? '#2563eb' : '#1e293b',
            color: '#f1f5f9',
            borderRadius: '0.75rem',
            padding: '0.5rem 0.875rem',
            fontSize: '0.875rem',
            whiteSpace: 'pre-wrap',
          }}>
            {msg.content}
          </div>
        ))}

        {/* status log during artifact generation */}
        {statusLines.length > 0 && <StatusLog lines={statusLines} />}

        {/* thinking indicator (non-artifact runs) */}
        {agentRunning && !generatingArtifacts && (
          <div style={{
            alignSelf: 'flex-start',
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            color: '#94a3b8',
            fontSize: '0.875rem',
            fontStyle: 'italic',
          }}>
            <Spinner />
            Agent is thinking…
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* ── input area ── */}
      <div style={{
        padding: '0.75rem 1rem',
        borderTop: '1px solid #333',
        display: 'flex',
        gap: '0.5rem',
        flexShrink: 0,
        background: agentRunning ? 'rgba(15,23,42,0.6)' : 'transparent',
        transition: 'background 0.2s',
      }}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={inputPlaceholder}
          disabled={agentRunning}
          rows={2}
          style={{
            flex: 1,
            background: '#1e293b',
            color: agentRunning ? '#64748b' : '#f1f5f9',
            border: `1px solid ${agentRunning ? '#1e3a5f' : '#334155'}`,
            borderRadius: '0.5rem',
            padding: '0.5rem',
            fontSize: '0.875rem',
            resize: 'none',
            outline: 'none',
            opacity: agentRunning ? 0.6 : 1,
            cursor: agentRunning ? 'not-allowed' : 'text',
            transition: 'opacity 0.2s, color 0.2s, border-color 0.2s',
          }}
        />
        <button
          onClick={sendMessage}
          disabled={agentRunning || !input.trim()}
          style={{
            background: agentRunning ? '#1e3a5f' : '#2563eb',
            color: agentRunning ? '#475569' : '#fff',
            border: 'none',
            borderRadius: '0.5rem',
            padding: '0.5rem 1rem',
            cursor: agentRunning || !input.trim() ? 'not-allowed' : 'pointer',
            opacity: agentRunning || !input.trim() ? 0.5 : 1,
            transition: 'background 0.2s, color 0.2s, opacity 0.2s',
            fontWeight: 500,
            fontSize: '0.875rem',
          }}
        >
          {agentRunning ? 'Working…' : 'Send'}
        </button>
      </div>
    </div>
  );
}