import { useState } from 'react'

export default function RecallSearch({ projectId }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [mode, setMode] = useState('recall')

  const run = async (e) => {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const endpoint = mode === 'recall'
        ? `/api/projects/${projectId}/recall`
        : `/api/projects/${projectId}/search`
      const body = mode === 'recall'
        ? { query: query.trim(), n: 5 }
        : { query: query.trim() }
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const d = await res.json()
        throw new Error(d.detail || `HTTP ${res.status}`)
      }
      setResults(await res.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const renderRecallResult = (r, i) => {
    const meta = r.metadata || {}
    const score = (1 - (r.distance || 0)).toFixed(2)
    const ts = (meta.timestamp || '').slice(0, 16).replace('T', ' ')
    return (
      <div key={i} className="recall-result">
        <div className="recall-result-header">
          <span>[{i + 1}]</span>
          <span>{meta.type || 'note'}</span>
          <span>score: {score}</span>
          {ts && <span>{ts}</span>}
        </div>
        <div className="recall-result-text">{(r.text || '').slice(0, 400)}</div>
      </div>
    )
  }

  const renderSearchResult = (r, i) => (
    <div key={i} className="recall-result">
      <div className="recall-result-header">
        <span>[{i + 1}]</span>
        <span style={{ color: 'var(--text)' }}>{r.title || '(no title)'}</span>
      </div>
      {r.url && <div style={{ fontSize: 11, color: 'var(--accent-2)', marginBottom: 4 }}>{r.url}</div>}
      {r.snippet && <div className="recall-result-text">{r.snippet.slice(0, 300)}</div>}
    </div>
  )

  return (
    <div>
      <form onSubmit={run} className="recall-form">
        <select value={mode} onChange={e => { setMode(e.target.value); setResults(null) }} style={{ width: 100 }}>
          <option value="recall">Recall</option>
          <option value="search">Web Search</option>
        </select>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={mode === 'recall' ? 'Search vector memory…' : 'Web search…'}
          autoFocus
        />
        <button type="submit" className="primary" disabled={loading || !query.trim()} style={{ width: 'auto' }}>
          {loading ? '…' : '→'}
        </button>
      </form>
      {error && <div className="error-msg">{error}</div>}
      {results && results.length === 0 && <div className="empty-state">No results found.</div>}
      {results && results.map((r, i) =>
        mode === 'recall' ? renderRecallResult(r, i) : renderSearchResult(r, i)
      )}
    </div>
  )
}
