import { LeaderboardEntry, Senator } from "@/types/senator";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

export async function fetchSenatorsByState(state: string): Promise<Senator[]> {
  const res = await fetch(`${API_BASE}/senators?state=${state}`);
  if (!res.ok) throw new Error(`Failed to load senators: ${res.status}`);
  return res.json();
}

export async function fetchSenator(senatorId: string): Promise<Senator> {
  const res = await fetch(`${API_BASE}/senators/${senatorId}`);
  if (!res.ok) throw new Error(`Senator not found: ${res.status}`);
  return res.json();
}

export interface StateInfo {
  code: string;
  name: string;
  senatorCount: number;
}

export async function fetchStates(): Promise<StateInfo[]> {
  const res = await fetch(`${API_BASE}/senators/states`);
  if (!res.ok) throw new Error(`Failed to load states: ${res.status}`);
  return res.json();
}

export async function fetchLeaderboard(): Promise<LeaderboardEntry[]> {
  const res = await fetch(`${API_BASE}/senators/leaderboard`);
  if (!res.ok) throw new Error(`Failed to load leaderboard: ${res.status}`);
  return res.json();
}

export async function fetchSenatorHighlights(senatorId: string): Promise<string[]> {
  const res = await fetch(`${API_BASE}/senators/${senatorId}/highlights`);
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data.highlights) ? data.highlights : [];
}

export interface IndustryInfo {
  name: string;
  color: string;
}

export interface AppConfig {
  scoreWeights: Record<string, number>;
  industries: Record<string, IndustryInfo>;
  platformCategories: Record<string, string>;
  policyAreas: string[];
}

const DEFAULT_CONFIG: AppConfig = {
  scoreWeights: {
    constituentFunding: 0.25,
    promiseFulfillment: 0.20,
    independenceIndex: 0.25,
    donorDiversity: 0.10,
    accountability: 0.20,
  },
  industries: {},
  platformCategories: {},
  policyAreas: [],
};

let _cachedConfig: AppConfig | null = null;

export interface PipelineRunInfo {
  id: number;
  startedAt: string;
  completedAt: string | null;
  status: string;
  currentPhase: string | null;
  senatorsProcessed: number;
  senatorsTotal: number;
  senatorsFailed: number;
  billsClassified: number;
  llmCalls: number;
  cacheHits: number;
  cacheMisses: number;
  elapsedSeconds: number | null;
  errorMessage: string | null;
}

export interface PipelineStatus {
  lastRun: PipelineRunInfo | null;
  nextScheduled: string | null;
  isRunning: boolean;
}

export async function fetchPipelineStatus(): Promise<PipelineStatus | null> {
  try {
    const res = await fetch(`${API_BASE}/pipeline/status`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function fetchConfig(): Promise<AppConfig> {
  if (_cachedConfig) return _cachedConfig;
  try {
    const res = await fetch(`${API_BASE}/config`);
    if (res.ok) {
      _cachedConfig = await res.json();
      return _cachedConfig!;
    }
  } catch {
    // Fall through to defaults
  }
  return DEFAULT_CONFIG;
}
