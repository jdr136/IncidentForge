export interface ScenarioMeta {
  id: string;
  version: number;
  title: string;
  environment: 'kubernetes' | 'aws';
  difficulty: 'beginner' | 'intermediate' | 'advanced';
  summary: string;
  target: string;
}

export interface ProviderInfo {
  provider_id: string;
  display_name: string;
  models: string[];
  available: boolean;
}

export interface RunResponse {
  run_id: string;
  scenario_id: string;
  scenario_version: number;
  provider: string;
  model: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  error_code: string | null;
  events_url: string;
}

export type RunStatus =
  | 'QUEUED' | 'PREPARING' | 'BASELINE_READY' | 'INJECTING'
  | 'FAULT_CONFIRMED' | 'INVESTIGATING' | 'DIAGNOSED'
  | 'AWAITING_VERIFICATION' | 'VERIFYING' | 'COMPLETED'
  | 'FAILED' | 'TIMED_OUT' | 'CANCELLED' | 'INTERRUPTED'
  | 'UNSUPPORTED' | 'INVALID_SCENARIO';

export interface RunEvent {
  id: number;
  run_id: string;
  seq: number;
  at: string;
  type: string;
  stage: RunStatus;
  message: string;
  payload: Record<string, unknown>;
}

export interface StartRunRequest {
  scenario_id: string;
  scenario_version: number;
  provider: string;
  model: string;
  seed?: number;
  temperature?: number;
}

export const TERMINAL_STATUSES: RunStatus[] = [
  'COMPLETED', 'FAILED', 'TIMED_OUT', 'CANCELLED',
  'INTERRUPTED', 'UNSUPPORTED', 'INVALID_SCENARIO',
];
