import { useState } from 'react'

export default function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        setError(d.detail || 'Login failed')
        return
      }
      const data = await res.json()
      onLogin(data)
    } catch {
      setError('Network error — is the server running?')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg)',
    }}>
      <form onSubmit={handleSubmit} style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        padding: '40px 36px',
        width: 340,
        display: 'flex',
        flexDirection: 'column',
        gap: 18,
        boxShadow: 'var(--shadow)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: 4 }}>
          <span style={{ fontSize: 30 }}>🌸</span>
          <h1 style={{ fontSize: 20, fontWeight: 700, marginTop: 8 }}>Orchid</h1>
          <p style={{ color: 'var(--text-dim)', fontSize: 13, marginTop: 4 }}>Sign in to continue</p>
        </div>

        <div className="field">
          <label htmlFor="username">Username</label>
          <input
            id="username"
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            autoFocus
            autoComplete="username"
            required
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </div>

        {error && (
          <p style={{ color: 'var(--error-fg)', fontSize: 13, textAlign: 'center', margin: '-4px 0' }}>
            {error}
          </p>
        )}

        <button
          type="submit"
          className="primary"
          disabled={loading}
          style={{ padding: '9px 0', fontSize: 14 }}
        >
          {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
