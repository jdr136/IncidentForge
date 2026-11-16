'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '../lib/api';
import type { ProviderInfo, ScenarioMeta } from '../lib/types';

interface Props {
  scenarios: ScenarioMeta[];
  providers: ProviderInfo[];
}

export default function Overview({ scenarios, providers }: Props) {
  const router = useRouter();
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null);
  const [selectedProvider, setSelectedProvider] = useState(providers[0]?.provider_id ?? '');
  const [selectedModel, setSelectedModel] = useState(providers[0]?.models[0] ?? '');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const availableProviders = providers.filter(p => p.available);
  const currentProvider = providers.find(p => p.provider_id === selectedProvider);

  const handleStart = async () => {
    if (!selectedScenario || !selectedProvider || !selectedModel) return;
    setLoading(true);
    setError(null);
    try {
      const run = await api.runs.start({
        scenario_id: selectedScenario,
        scenario_version: 1,
        provider: selectedProvider,
        model: selectedModel,
        temperature: 0,
      });
      router.push(`/runs/${run.run_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start run');
      setLoading(false);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <div className="page-title">Scenario Library</div>
        <div className="page-subtitle">Select a scenario and model to begin an evaluation run.</div>
      </div>

      {!availableProviders.length && (
        <div className="env-warning">
          ⚠️ No LLM provider configured. Set <code>OPENAI_API_KEY</code> in <code>.env</code> to run real investigations. Demo stub mode is active.
        </div>
      )}

      {/* Scenario cards */}
      <div className="section-title">Scenarios</div>
      <div className="grid-3" style={{ marginBottom: 24 }}>
        {scenarios.map(s => (
          <div
            key={s.id}
            className="card"
            onClick={() => setSelectedScenario(s.id)}
            style={{
              cursor: 'pointer',
              borderColor: selectedScenario === s.id ? 'var(--accent)' : undefined,
              outline: selectedScenario === s.id ? '1px solid var(--accent)' : undefined,
            }}
          >
            <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
              <span className={`badge badge-${s.environment}`}>{s.environment}</span>
              <span className={`badge badge-${s.difficulty}`}>{s.difficulty}</span>
            </div>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>{s.title}</div>
            <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>{s.summary}</div>
          </div>
        ))}
        {!scenarios.length && (
          <div className="empty-state">No scenarios loaded. Is the API running?</div>
        )}
      </div>

      {/* Model selector + run */}
      <div className="card" style={{ maxWidth: 480 }}>
        <div className="section-title">Start a run</div>
        <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
          <div style={{ flex: 1 }}>
            <label>Provider</label>
            <select
              value={selectedProvider}
              onChange={e => {
                setSelectedProvider(e.target.value);
                const p = providers.find(prov => prov.provider_id === e.target.value);
                setSelectedModel(p?.models[0] ?? '');
              }}
              style={{ width: '100%' }}
            >
              {providers.map(p => (
                <option key={p.provider_id} value={p.provider_id} disabled={!p.available}>
                  {p.display_name}{!p.available ? ' (not configured)' : ''}
                </option>
              ))}
              {!providers.length && <option>No providers available</option>}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label>Model</label>
            <select
              value={selectedModel}
              onChange={e => setSelectedModel(e.target.value)}
              style={{ width: '100%' }}
            >
              {(currentProvider?.models ?? []).map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
        </div>

        {error && (
          <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 10 }}>{error}</div>
        )}

        <button
          className="primary"
          onClick={handleStart}
          disabled={!selectedScenario || loading || !selectedProvider}
          style={{ width: '100%' }}
        >
          {loading ? 'Starting…' : selectedScenario ? `Run: ${selectedScenario}` : 'Select a scenario first'}
        </button>
      </div>
    </div>
  );
}
