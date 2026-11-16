import type { ProviderInfo, RunResponse, ScenarioMeta, StartRunRequest } from './types';

const BASE = '/api/v1';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err?.detail?.message ?? err?.detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  scenarios: {
    list: () => request<ScenarioMeta[]>('/scenarios'),
  },
  providers: {
    list: () => request<ProviderInfo[]>('/providers'),
  },
  runs: {
    start: (body: StartRunRequest) =>
      request<RunResponse>('/runs', { method: 'POST', body: JSON.stringify(body) }),
    get: (id: string) => request<RunResponse>(`/runs/${id}`),
    list: () => request<RunResponse[]>('/runs'),
    verify: (id: string) =>
      request<{ run_id: string; status: string }>(`/runs/${id}/verify`, { method: 'POST' }),
    cancel: (id: string) =>
      request<{ run_id: string; status: string }>(`/runs/${id}/cancel`, { method: 'POST' }),
  },
};
