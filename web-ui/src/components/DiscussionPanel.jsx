import React, { useState, useEffect, useRef } from 'react';

export default function DiscussionPanel({ project, ws }) {
  const [messages, setMessages] = useState([
    { role: 'assistant', content: "Hi! I'm your AI project manager. Tell me about the project you want to build — what it does, who it's for, and any technical preferences you have." }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!ws) return;
    const handler = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'pm_message') {
          setMessages(prev => [...prev, { role: 'assistant', content: msg.content }]);
          setLoading(false);
        }
        if (msg.type === 'artifacts_ready') {
          setLoading(false);
        }
      } catch {}
    };
    ws.addEventListener('message', handler);
    return () => ws.removeEventListener('message', handler);
  }, [ws]);

  const send = async () => {
    if (!input.trim() || loading) return;
    const userMsg = input.trim();
    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setLoading(true);

    try {
      await fetch(`/api/projects/${project.id}/discuss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg }),
      });
    } catch (e) {
      setLoading(false);
    }
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      flex: 1,
      minHeight: 0,
      height: '100%',
    }}>
      {/* Messages — scrollable area */}
      <div style={{
        flex: 1,
        minHeight: 0,
        overflowY: 'auto',
        padding: '16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
      }}>
        {messages.map((m, i) => (
          <div key={i} style={{
            display: 'flex',
            justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
          }}>
            <div style={{
              maxWidth: '75%',
              padding: '10px 14px',
              borderRadius: m.role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
              background: m.role === 'user' ? '#6366f1' : '#f3f4f6',
              color: m.role === 'user' ? 'white' : '#1f2937',
              fontSize: '14px',
              lineHeight: '1.5',
              whiteSpace: 'pre-wrap',
            }}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
            <div style={{
              padding: '10px 14px',
              borderRadius: '18px 18px 18px 4px',
              background: '#f3f4f6',
              color: '#6b7280',
              fontSize: '14px',
            }}>
              <span>●●●</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input — fixed at bottom */}
      <div style={{
        padding: '12px 16px',
        borderTop: '1px solid #e5e7eb',
        display: 'flex',
        gap: '8px',
        flexShrink: 0,
        background: 'white',
      }}>
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
          placeholder="Describe your project..."
          style={{
            flex: 1,
            padding: '10px 14px',
            borderRadius: '24px',
            border: '1px solid #d1d5db',
            outline: 'none',
            fontSize: '14px',
          }}
        />
        <button
          onClick={send}
          disabled={loading || !input.trim()}
          style={{
            padding: '10px 20px',
            borderRadius: '24px',
            border: 'none',
            background: loading || !input.trim() ? '#d1d5db' : '#6366f1',
            color: 'white',
            cursor: loading || !input.trim() ? 'not-allowed' : 'pointer',
            fontWeight: '600',
          }}
        >Send</button>
      </div>
    </div>
  );
}