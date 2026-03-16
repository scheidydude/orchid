import { useState, useCallback } from 'react'
import { useWebSocket } from './useWebSocket.js'

const MAX_ENTRIES = 500

export function useAgentStream(projectId) {
  const [entries, setEntries] = useState([])
  const [runStatus, setRunStatus] = useState({ running: false, current_task: '', tasks_done: 0 })

  const handleMessage = useCallback((msg) => {
    const ts = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })

    setEntries(prev => {
      const entry = { ts, type: msg.type, data: msg.data, id: Date.now() + Math.random() }
      const next = [...prev, entry]
      return next.length > MAX_ENTRIES ? next.slice(next.length - MAX_ENTRIES) : next
    })

    // Update run status from events
    if (msg.type === 'session_start') {
      setRunStatus(s => ({ ...s, running: true, tasks_done: 0 }))
    } else if (msg.type === 'task_start') {
      setRunStatus(s => ({ ...s, running: true, current_task: msg.data?.task_id || '' }))
    } else if (msg.type === 'task_complete') {
      setRunStatus(s => ({ ...s, tasks_done: msg.data?.done_so_far || s.tasks_done + 1 }))
    } else if (msg.type === 'session_complete') {
      setRunStatus(s => ({ ...s, running: false, current_task: '' }))
    } else if (msg.type === 'connected') {
      setRunStatus(s => ({ ...s, running: msg.data?.running || false, current_task: msg.data?.current_task || '' }))
    }
  }, [])

  useWebSocket(projectId, handleMessage)

  const clear = useCallback(() => setEntries([]), [])

  return { entries, runStatus, clear }
}
