import { api } from '../../lib/api';
import type { RunResponse } from '../../lib/types';

export const dynamic = 'force-dynamic';

function statusColor(s: string) {
  if (s === 'COMPLETED') return 'var(--green)';
  if (['FAILED', 'TIMED_OUT', 'INVALID_SCENARIO'].includes(s)) return 'var(--red)';
  return 'var(--text-muted)';
}

export default async function BenchmarksPage() {
  let runs: RunResponse[] = [];
  try { runs = await api.runs.list(); } catch {}

  return (
    <div className="page">
      <div className="page-header">
        <div className="page-title">Benchmark History</div>
        <div className="page-subtitle">{runs.length} run{runs.length !== 1 ? 's' : ''} recorded.</div>
      </div>

      {!runs.length ? (
        <div className="card empty-state">No runs yet. Start a scenario from the overview.</div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface-2)' }}>
                {['Run ID', 'Scenario', 'Model', 'Status', 'Started'].map(h => (
                  <th key={h} style={{ padding: '10px 14px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.run_id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 14px', fontFamily: 'var(--mono)', fontSize: 11 }}>
                    <a href={`/runs/${r.run_id}`}>{r.run_id.slice(0, 20)}…</a>
                  </td>
                  <td style={{ padding: '8px 14px' }}>{r.scenario_id}</td>
                  <td style={{ padding: '8px 14px', color: 'var(--text-muted)' }}>{r.model}</td>
                  <td style={{ padding: '8px 14px', color: statusColor(r.status), fontWeight: 600 }}>{r.status}</td>
                  <td style={{ padding: '8px 14px', color: 'var(--text-muted)' }}>
                    {new Date(r.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
