import { api } from '../lib/api';
import type { ProviderInfo, ScenarioMeta } from '../lib/types';
import Overview from '../components/Overview';

export const dynamic = 'force-dynamic';

export default async function HomePage() {
  let scenarios: ScenarioMeta[] = [];
  let providers: ProviderInfo[] = [];
  try {
    [scenarios, providers] = await Promise.all([api.scenarios.list(), api.providers.list()]);
  } catch {
    // API not available yet
  }
  return <Overview scenarios={scenarios} providers={providers} />;
}
