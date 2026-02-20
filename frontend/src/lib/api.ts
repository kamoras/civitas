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
