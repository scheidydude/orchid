import { h } from 'https://esm.sh/preact@10.19.2';
import { useState, useEffect, useRef, useCallback } from 'https://esm.sh/preact@10.19.2/hooks';

export default function DiscussionPanel({ projectId, projectPath }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isConnected, setIsConnected] = useState(false);
  const [agentTyping, setAgentTyping] = useState(false);
  const wsRef = useRef(null);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const reconnectTimerRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Focus the input whenever agentTyping transitions from true -> false
  // (i.e. agent finished responding)
  useEffect(() => {
    if (!agentTyping) {
      // Small timeout so the DOM has settled before we steal focus
      const t = setTimeout(() => {
        inputRef.current?.focus();
      }, 50);
      return () => clearTimeout(t);
    }
  }, [agentTyping]);

  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const wsUrl = `ws://${window.location.host}/ws/discussion/${projectId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      // Focus input once connected
      setTimeout(() => inputRef.current?.focus(), 50);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'message') {
        setMessages(prev => [...prev, { role: 'assistant', content: data.content }]);
        setAgentTyping(false);
        // Re-focus input after agent responds
        setTimeout(() => inputRef.current?.focus(), 50);
      } else if (data.type === 'typing') {
        setAgentTyping(true);
      } else if (data.type === 'error') {
        setMessages(prev => [...prev, { role: 'error', content: data.content }]);
        setAgentTyping(false);
        // Re-focus input after error too
        setTimeout(() => inputRef.current?.focus(), 50);
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      reconnectTimerRef.current = setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [projectId]);

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connectWebSocket]);

  const sendMessage = useCallback(() => {
    const text = input.trim();
    if (!text || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    setMessages(prev => [...prev, { role: 'user', content: text }]);
    wsRef.current.send(JSON.stringify({ type: 'message', content: text }));
    setInput('');
    setAgentTyping(true);
    // Keep focus on input after sending
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [input]);

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }, [sendMessage]);

  // Clicking anywhere in the input wrapper area focuses the textarea
  const handleInputAreaClick = useCallback(() => {
    inputRef.current?.focus();
  }, []);

  return h('div', { style: { display: 'flex', flexDirection: 'column', height: '100%', fontFamily: 'sans-serif' } },
    // Header
    h('div', { style: { padding: '12px 16px', borderBottom: '1px solid #e5e7eb', display: 'flex', alignItems: 'center', gap: '8px' } },
      h('div', { style: { width: '8px', height: '8px', borderRadius: '50%', background: isConnected ? '#10b981' : '#ef4444' } }),
      h('span', { style: { fontSize: '14px', color: '#6b7280' } }, isConnected ? 'Connected' : 'Reconnecting...')
    ),

    // Messages
    h('div', { style: { flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' } },
      messages.length === 0 && h('div', { style: { textAlign: 'center', color: '#9ca3af', marginTop: '40px' } },
        h('p', null, '💬 Start a conversation with your PM agent'),
        h('p', { style: { fontSize: '13px', marginTop: '8px' } }, 'Describe your project idea and the agent will help you plan it')
      ),
      messages.map((msg, i) =>
        h('div', { key: i, style: { display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start' } },
          h('div', {
            style: {
              maxWidth: '75%',
              padding: '10px 14px',
              borderRadius: msg.role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
              background: msg.role === 'user' ? '#6366f1' : msg.role === 'error' ? '#fee2e2' : '#f3f4f6',
              color: msg.role === 'user' ? 'white' : msg.role === 'error' ? '#dc2626' : '#1f2937',
              fontSize: '14px',
              lineHeight: '1.5',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word'
            }
          }, msg.content)
        )
      ),
      agentTyping && h('div', { style: { display: 'flex', justifyContent: 'flex-start' } },
        h('div', { style: { padding: '10px 14px', borderRadius: '18px 18px 18px 4px', background: '#f3f4f6', color: '#6b7280', fontSize: '14px' } },
          '...'
        )
      ),
      h('div', { ref: messagesEndRef })
    ),

    // Input area — clicking anywhere in this wrapper focuses the textarea
    h('div', {
      onClick: handleInputAreaClick,
      style: {
        padding: '12px 16px',
        borderTop: '1px solid #e5e7eb',
        display: 'flex',
        gap: '8px',
        alignItems: 'flex-end',
        cursor: 'text'
      }
    },
      h('textarea', {
        ref: inputRef,
        value: input,
        onInput: (e) => setInput(e.target.value),
        onKeyDown: handleKeyDown,
        placeholder: 'Type a message...',
        rows: 1,
        style: {
          flex: 1,
          padding: '10px 14px',
          borderRadius: '20px',
          border: '1px solid #d1d5db',
          outline: 'none',
          resize: 'none',
          fontSize: '14px',
          lineHeight: '1.5',
          fontFamily: 'inherit',
          overflowY: 'hidden',
          maxHeight: '120px',
          cursor: 'text'
        }
      }),
      h('button', {
        onClick: (e) => {
          e.stopPropagation(); // don't let the wrapper's onClick interfere
          sendMessage();
        },
        disabled: !input.trim() || !isConnected,
        style: {
          padding: '10px 16px',
          borderRadius: '20px',
          border: 'none',
          background: input.trim() && isConnected ? '#6366f1' : '#e5e7eb',
          color: input.trim() && isConnected ? 'white' : '#9ca3af',
          cursor: input.trim() && isConnected ? 'pointer' : 'not-allowed',
          fontSize: '14px',
          fontWeight: '600',
          transition: 'all 0.2s'
        }
      }, 'Send')
    )
  );
}