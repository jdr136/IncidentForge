'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { api } from '../../../lib/api';
import type { RunEvent, RunResponse, RunStatus } from '../../../lib/types';
import { TERMINAL_STATUSES } from '../../../lib/types';

function statusDot(status: RunStatus) {
  if (TERMINAL_STATUSES.includes(status)) {
    return status === 'COMPLETED' ? 'dot-ok' : 'dot-fail';
  }
  return 'dot-running';
}

function formatTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function RunPage() {
  const params = useParams();
  const runId = params.id as string;
  const [run, setRun] = useState<RunResponse | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Poll run status
  useEffect(() => {
    const poll = async () => {
      try {
        const r = await api.runs.get(runId);
        setRun(r);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load run');
      }
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  }, [runId]);

  // SSE event stream
  useEffect(() => {
    const es = new EventSource(`/api/v1/runs/${runId}/events`);
    es.onmessage = (e) => {
      try {
        const event: RunEvent = JSON.parse(e.data);
        setEvents(prev => {
          if (prev.some(x => x.seq === event.seq)) return prev;
          return [...prev, event].sort((a, b) => a.seq - b.seq);
        });
        // Update run status from event stage
        setRun(prev => prev ? { ...prev, status: event.stage } : prev);
        if (TERMINAL_STATUSES.includes(event.stage)) es.close();
      } catch {
        // ignore parse errors
      }
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [runId]);

  const handleVerify = async () => {
    setVerifying(true);
    try {
      await api.runs.verify(runId);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Verification failed');
      setVerifying(false);
    }
  };

  // Extract diagnosis from events
  const diagnosisEvent = [...events].reverse().find(e => e.type === 'diagnosis_completed');
  const diagnosis = diagnosisEvent?.payload?.diagnosis as Record<string, unknown> | undefined;
  const evalEvent = [...events].reverse().find(e => e.type === 'evaluation_completed');
  const evaluation = evalEvent?.payload?.evaluation as Record<string, unknown> | undefined;

  return (
    <div className="page">
      {error && <div style={{ color: 'var(--red)', marginBottom: 12 }}>{error}</div>}

      {/* Header */}
      <div className="page-header" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div className="page-title">
            <span className={`status-dot ${run ? statusDot(run.status) : 'dot-idle'}`} />
            {run?.scenario_id ?? 'Loading…'}
          </div>
          <div className="page-subtitle">
            {run?.run_id} · {run?.provider}/{run?.model} · {run?.status}
          </div>
        </div>
        {run?.status === 'AWAITING_VERIFICATION' && (
          <button className="primary" onClick={handleVerify} disabled={verifying}>
            {verifying ? 'Verifying…' : '✓ Verify fix in sandbox'}
          </button>
        )}
      </div>

      <div className="grid-2" style={{ gap: 16 }}>
        {/* Event timeline */}
        <div>
          <div className="section-title">Event timeline</div>
          <div className="card">
            <div className="timeline">
              {events.length === 0 && (
                <div className="empty-state">Waiting for events…</div>
              )}
              {events.map(ev => (
                <div key={ev.seq} className="timeline-item">
                  <span className="timeline-time">{formatTime(ev.at)}</span>
                  <span className="timeline-stage">{ev.stage}</span>
                  <span className="timeline-msg">
                    {ev.message}
                    {ev.type === 'tool_observation' && Array.isArray(ev.payload?.evidence_ids) && (
                      <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 6 }}>
                        [{(ev.payload.evidence_ids as string[]).join(', ')}]
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right panel: diagnosis + evaluation */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* Diagnosis */}
          {diagnosis && (
            <div className="card">
              <div className="section-title">AI Diagnosis</div>
              <div style={{ marginBottom: 8 }}>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Root cause</span>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--accent)', marginTop: 2 }}>
                  {diagnosis.root_cause_id as string}
                </div>
              </div>
              <div style={{ fontSize: 13, color: 'var(--text-dim)', marginBottom: 10 }}>
                {diagnosis.summary as string}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                Confidence: {((diagnosis.confidence as number) * 100).toFixed(0)}%
              </div>
              {Array.isArray(diagnosis.evidence_ids) && (
                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  Evidence: {(diagnosis.evidence_ids as string[]).join(', ')}
                </div>
              )}
              {diagnosis.remediation !== undefined && (
                <div style={{ marginTop: 10 }}>
                  <div className="section-title">Proposed fix</div>
                  <div className="code-block">
                    {JSON.stringify(diagnosis.remediation, null, 2)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--yellow)', marginTop: 6 }}>
                    ⚠️ Proposed fix — not yet verified in sandbox
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Evaluation scores */}
          {evaluation && (
            <div className="card">
              <div className="section-title">Evaluation</div>
              <div style={{
                display: 'inline-block', padding: '3px 10px', borderRadius: 4,
                background: evaluation.verification_status === 'passed' ? '#14532d' : '#4c0519',
                color: evaluation.verification_status === 'passed' ? 'var(--green)' : 'var(--red)',
                fontSize: 12, fontWeight: 600, marginBottom: 12,
              }}>
                {evaluation.verification_status === 'passed' ? '✓ Verified fix' : evaluation.verification_status === 'failed' ? '✗ Fix failed' : '~ Stub mode'}
              </div>
              {/* Show stub scores if present */}
              {Object.entries(evaluation).filter(([k]) => k !== 'verification_status' && k !== 'stub').map(([key, val]) => {
                if (typeof val !== 'object' || val === null) return null;
                const v = val as { score: number; max: number; status: string };
                if (!('score' in v)) return null;
                return (
                  <div key={key} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
                    <span style={{ color: 'var(--text-muted)', textTransform: 'capitalize' }}>{key.replace(/_/g, ' ')}</span>
                    <span>
                      {v.status === 'not_evaluated'
                        ? <span style={{ color: 'var(--text-muted)' }}>—</span>
                        : <span><strong>{v.score}</strong><span style={{ color: 'var(--text-muted)' }}>/{v.max}</span></span>}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {!diagnosis && !TERMINAL_STATUSES.includes((run?.status ?? 'QUEUED') as RunStatus) && (
            <div className="card empty-state">Diagnosis will appear here…</div>
          )}
        </div>
      </div>
    </div>
  );
}
