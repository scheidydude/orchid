import { useState, useEffect } from 'react';

export function ArtifactPanel({ project, artifacts, phase, onPhaseChange, onRefresh }) {
  const [selectedArtifact, setSelectedArtifact] = useState(null);
  const [artifactContent, setArtifactContent] = useState('');
  const [loadingContent, setLoadingContent] = useState(false);
  const [approving, setApproving] = useState(false);

  useEffect(() => {
    if (artifacts.length > 0 && !selectedArtifact) {
      setSelectedArtifact(artifacts[0]);
      fetchArtifactContent(artifacts[0]);
    }
  }, [artifacts]);

  const fetchArtifactContent = async (artifact) => {
    setLoadingContent(true);
    try {
      const resp = await fetch(`/api/projects/${project.id}/artifacts/${encodeURIComponent(artifact.name)}`);
      if (resp.ok) {
        const data = await resp.json();
        setArtifactContent(data.content || '');
      }
    } catch (e) {
      console.error('Failed to fetch artifact content:', e);
    } finally {
      setLoadingContent(false);
    }
  };

  const handleApprove = async () => {
    setApproving(true);
    try {
      const resp = await fetch(`/api/projects/${project.id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved: true }),
      });
      if (resp.ok) onPhaseChange('approved');
    } catch (e) {
      console.error('Failed to approve:', e);
    } finally {
      setApproving(false);
    }
  };

  if (artifacts.length === 0) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        color: '#666',
        flexDirection: 'column',
        gap: '12px',
      }}>
        <div style={{ fontSize: '48px' }}>📄</div>
        <div style={{ fontSize: '16px' }}>No artifacts yet</div>
        <div style={{ fontSize: '13px', color: '#555' }}>
          Complete the discussion phase to generate project artifacts
        </div>
        <button
          onClick={onRefresh}
          style={{
            marginTop: '8px',
            padding: '8px 16px',
            background: '#2a2a2a',
            color: '#888',
            border: '1px solid #444',
            borderRadius: '6px',
            cursor: 'pointer',
            fontSize: '13px',
          }}
        >
          🔄 Refresh
        </button>
      </div>
    );
  }

  return (
    /* Outer row: fills the flex cell from PlanningTab.
       flex: 1 + minHeight: 0 lets the row shrink so children can scroll. */
    <div style={{
      display: 'flex',
      flex: 1,
      minHeight: 0,
      gap: '16px',
      overflow: 'hidden',
    }}>
      {/* Sidebar — scrollable list of artifacts + approve button */}
      <div style={{
        width: '200px',
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        overflowY: 'auto',   /* scroll if many artifacts */
        minHeight: 0,
      }}>
        <div style={{
          fontSize: '12px', color: '#888', marginBottom: '4px',
          textTransform: 'uppercase', letterSpacing: '0.5px',
          flexShrink: 0,
        }}>
          Artifacts
        </div>

        {artifacts.map(artifact => (
          <div
            key={artifact.name}
            onClick={() => { setSelectedArtifact(artifact); fetchArtifactContent(artifact); }}
            style={{
              padding: '8px 12px',
              borderRadius: '6px',
              cursor: 'pointer',
              flexShrink: 0,
              background: selectedArtifact?.name === artifact.name ? '#2a3a4a' : '#1e1e1e',
              border: `1px solid ${selectedArtifact?.name === artifact.name ? '#4a90d9' : '#333'}`,
              color: selectedArtifact?.name === artifact.name ? '#e0e0e0' : '#aaa',
              fontSize: '13px',
            }}
          >
            <div style={{ fontWeight: 500 }}>{artifact.name}</div>
            <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
              {new Date(artifact.modified * 1000).toLocaleDateString()}
            </div>
          </div>
        ))}

        {/* Approval button pinned to bottom of sidebar */}
        {phase !== 'approved' ? (
          <div style={{ marginTop: 'auto', paddingTop: '16px', flexShrink: 0 }}>
            <button
              onClick={handleApprove}
              disabled={approving}
              style={{
                width: '100%', padding: '10px', background: '#4CAF50',
                color: '#fff', border: 'none', borderRadius: '6px',
                cursor: 'pointer', fontSize: '13px', fontWeight: 600,
              }}
            >
              {approving ? 'Approving...' : '✅ Approve Plan'}
            </button>
          </div>
        ) : (
          <div style={{
            marginTop: 'auto', padding: '10px', background: '#1a3a1a',
            border: '1px solid #4CAF50', borderRadius: '6px',
            color: '#4CAF50', fontSize: '13px', textAlign: 'center',
            flexShrink: 0,
          }}>
            ✅ Plan Approved
          </div>
        )}
      </div>

      {/* Content area — scrollable pre */}
      <div style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {loadingContent ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: '100%', color: '#666',
          }}>
            Loading...
          </div>
        ) : (
          <pre style={{
            flex: 1,
            minHeight: 0,      /* ← allows pre to shrink and scroll */
            margin: 0,
            padding: '16px',
            background: '#1e1e1e',
            borderRadius: '8px',
            border: '1px solid #333',
            color: '#e0e0e0',
            fontSize: '13px',
            lineHeight: '1.6',
            overflowY: 'auto', /* scroll long content */
            overflowX: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontFamily: 'monospace',
          }}>
            {artifactContent}
          </pre>
        )}
      </div>
    </div>
  );
}