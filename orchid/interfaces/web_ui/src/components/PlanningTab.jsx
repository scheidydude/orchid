import { useState, useEffect } from 'react';
import { DiscussionPanel } from './DiscussionPanel';
import { ArtifactPanel } from './ArtifactPanel';
import { ApprovalPanel } from './ApprovalPanel';

export function PlanningTab({ project, onPhaseChange }) {
  const [phase, setPhase] = useState(project?.phase || 'discussion');
  const [artifacts, setArtifacts] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (project?.id) fetchArtifacts();
  }, [project?.id]);

  const fetchArtifacts = async () => {
    try {
      const resp = await fetch(`/api/projects/${project.id}/artifacts`);
      if (resp.ok) {
        const data = await resp.json();
        setArtifacts(data.artifacts || []);
      }
    } catch (e) {
      console.error('Failed to fetch artifacts:', e);
    }
  };

  const handlePhaseChange = async (newPhase) => {
    setLoading(true);
    try {
      const resp = await fetch(`/api/projects/${project.id}/phase`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phase: newPhase }),
      });
      if (resp.ok) {
        setPhase(newPhase);
        if (onPhaseChange) onPhaseChange(newPhase);
      }
    } catch (e) {
      console.error('Failed to change phase:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleArtifactsReady = (newArtifacts) => {
    setArtifacts(newArtifacts);
    setPhase('review');
  };

  const phaseLabels = {
    discussion: '💬 Discussion',
    generating: '⚙️ Generating',
    review: '📋 Review',
    approved: '✅ Approved',
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      minHeight: 0,
      padding: '16px',
      gap: '16px',
    }}>
      {/* Phase indicator — fixed height, never shrinks */}
      <div style={{
        display: 'flex',
        gap: '8px',
        alignItems: 'center',
        flexShrink: 0,
      }}>
        <span style={{ color: '#888', fontSize: '13px' }}>Phase:</span>
        {Object.entries(phaseLabels).map(([p, label]) => (
          <span
            key={p}
            onClick={() => phase !== p && handlePhaseChange(p)}
            style={{
              padding: '4px 12px',
              borderRadius: '12px',
              fontSize: '12px',
              background: phase === p ? '#4CAF50' : '#2a2a2a',
              color: phase === p ? '#fff' : '#888',
              cursor: phase !== p ? 'pointer' : 'default',
              border: `1px solid ${phase === p ? '#4CAF50' : '#444'}`,
            }}
          >
            {label}
          </span>
        ))}
      </div>

      {/* Main content area — takes all remaining height, clips overflow so
          child panels handle their own internal scrolling */}
      <div style={{
        flex: 1,
        minHeight: 0,          /* ← critical: lets flex child shrink below content size */
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {phase === 'discussion' || phase === 'generating' ? (
          <DiscussionPanel project={project} onArtifactsReady={handleArtifactsReady} />
        ) : phase === 'review' ? (
          <ArtifactPanel
            project={project}
            artifacts={artifacts}
            phase={phase}
            onPhaseChange={handlePhaseChange}
            onRefresh={fetchArtifacts}
          />
        ) : (
          <ApprovalPanel
            project={project}
            artifacts={artifacts}
            phase={phase}
            onPhaseChange={handlePhaseChange}
            onRefresh={fetchArtifacts}
          />
        )}
      </div>
    </div>
  );
}