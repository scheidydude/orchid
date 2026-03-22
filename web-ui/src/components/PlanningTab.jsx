import React, { useState, useEffect } from 'react';
import DiscussionPanel from './DiscussionPanel';
import ArtifactPanel from './ArtifactPanel';
import ApprovalPanel from './ApprovalPanel';

export default function PlanningTab({ project, ws }) {
  const [phase, setPhase] = useState('discussion'); // discussion | artifacts | approval
  const [artifacts, setArtifacts] = useState([]);
  const [approved, setApproved] = useState(false);

  useEffect(() => {
    if (!project) return;
    fetch(`/api/projects/${project.id}/artifacts`)
      .then(r => r.json())
      .then(data => {
        setArtifacts(data.artifacts || []);
        if ((data.artifacts || []).length > 0) setPhase('artifacts');
      })
      .catch(() => {});
  }, [project]);

  useEffect(() => {
    if (!ws) return;
    const handler = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'artifacts_ready') {
          fetch(`/api/projects/${project.id}/artifacts`)
            .then(r => r.json())
            .then(data => {
              setArtifacts(data.artifacts || []);
              setPhase('artifacts');
            });
        }
        if (msg.type === 'artifacts_approved') {
          setApproved(true);
        }
      } catch {}
    };
    ws.addEventListener('message', handler);
    return () => ws.removeEventListener('message', handler);
  }, [ws, project]);

  const phases = [
    { id: 'discussion', label: '💬 Discussion' },
    { id: 'artifacts', label: '📄 Artifacts' },
    { id: 'approval', label: '✅ Approval' },
  ];

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      minHeight: 0,
    }}>
      {/* Phase tabs */}
      <div style={{
        display: 'flex',
        gap: '8px',
        padding: '12px 16px',
        borderBottom: '1px solid #e5e7eb',
        background: '#f9fafb',
        flexShrink: 0,
      }}>
        {phases.map(p => (
          <button
            key={p.id}
            onClick={() => setPhase(p.id)}
            style={{
              padding: '6px 16px',
              borderRadius: '20px',
              border: 'none',
              cursor: 'pointer',
              fontWeight: phase === p.id ? '600' : '400',
              background: phase === p.id ? '#6366f1' : '#e5e7eb',
              color: phase === p.id ? 'white' : '#374151',
              transition: 'all 0.2s',
            }}
          >{p.label}</button>
        ))}
      </div>

      {/* Panel content — flex: 1 + minHeight: 0 lets children scroll */}
      <div style={{
        flex: 1,
        minHeight: 0,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}>
        {phase === 'discussion' && <DiscussionPanel project={project} ws={ws} />}
        {phase === 'artifacts' && <ArtifactPanel artifacts={artifacts} />}
        {phase === 'approval' && <ApprovalPanel project={project} artifacts={artifacts} approved={approved} setApproved={setApproved} ws={ws} />}
      </div>
    </div>
  );
}