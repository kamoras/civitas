import { LeaderboardEntry, Senator } from "@/types/senator";
import type { President, PresidentLeaderboardEntry } from "@/types/president";

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
  presidentScoreWeights: Record<string, number>;
  industries: Record<string, IndustryInfo>;
  platformCategories: Record<string, string>;
  policyAreas: string[];
}

const DEFAULT_CONFIG: AppConfig = {
  scoreWeights: {
    fundingIndependence: 0.25,
    promisePersistence: 0.25,
    independentVoting: 0.25,
    transparency: 0.15,
    accessibility: 0.10,
  },
  presidentScoreWeights: {
    independence: 0.20,
    followThrough: 0.25,
    publicMandate: 0.20,
    effectiveness: 0.20,
    competence: 0.15,
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

function snakeToCamel(s: string): string {
  return s.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

function camelizeKeys(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(camelizeKeys);
  if (obj !== null && typeof obj === "object") {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([k, v]) => [
        snakeToCamel(k),
        camelizeKeys(v),
      ]),
    );
  }
  return obj;
}

export async function fetchPresidentLeaderboard(): Promise<PresidentLeaderboardEntry[]> {
  const res = await fetch(`${API_BASE}/presidents/leaderboard`);
  if (!res.ok) throw new Error(`Failed to load president leaderboard: ${res.status}`);
  const raw = await res.json();
  return camelizeKeys(raw) as PresidentLeaderboardEntry[];
}

export async function fetchPresident(id: string): Promise<President> {
  const res = await fetch(`${API_BASE}/presidents/${id}`);
  if (!res.ok) throw new Error(`President not found: ${res.status}`);
  const raw = await res.json();
  return camelizeKeys(raw) as President;
}

export interface ExploreResult {
  id: number;
  title: string;
  date: string;
  docType: string;
  source: string;
  politicianName: string;
  politicianId: string;
  chamber: string;
  distance: number;
  snippet: string;
  url: string;
  summary: string;
  agencyName: string;
  commentUrl: string;
  commentsCloseOn: string;
}

export interface ExploreResponse {
  query: string;
  results: ExploreResult[];
  count: number;
}

export interface ExploreStats {
  totalDocuments: number;
  byType: Record<string, number>;
  byChamber: Record<string, number>;
  openForComment: number;
}

export async function searchExplore(
  query: string,
  options?: { docType?: string; chamber?: string; limit?: number; commentableOnly?: boolean; sort?: "relevance" | "date" },
): Promise<ExploreResponse> {
  const params = new URLSearchParams({ q: query });
  if (options?.docType) params.set("doc_type", options.docType);
  if (options?.chamber) params.set("chamber", options.chamber);
  if (options?.limit) params.set("limit", String(options.limit));
  if (options?.commentableOnly) params.set("commentable", "true");
  if (options?.sort) params.set("sort", options.sort);

  const res = await fetch(`${API_BASE}/explore?${params}`);
  if (!res.ok) throw new Error(`Explore search failed: ${res.status}`);
  return res.json();
}

export interface ExploreDocumentDetail {
  id: number;
  title: string;
  summary: string;
  body: string;
  date: string;
  docType: string;
  source: string;
  url: string;
  politicianName: string;
  politicianId: string;
  chamber: string;
  agencyName: string;
  commentUrl: string;
  commentsCloseOn: string;
}

export interface ExploreDocumentSummary {
  relevance: string;
  keyPoints: string[];
  impact: string;
}

export async function fetchExploreDocument(id: number): Promise<ExploreDocumentDetail> {
  const res = await fetch(`${API_BASE}/explore/${id}`);
  if (!res.ok) throw new Error(`Document not found: ${res.status}`);
  return res.json();
}

export async function fetchExploreDocumentSummary(
  id: number,
  query: string,
): Promise<ExploreDocumentSummary> {
  const params = new URLSearchParams({ q: query });
  const res = await fetch(`${API_BASE}/explore/${id}/summary?${params}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Summary failed: ${res.status}`);
  return res.json();
}

export interface PublicComment {
  id: string;
  title: string;
  body: string;
  postedDate: string;
  submitterName: string;
  organization: string;
  category: string;
}

export interface CommentsResponse {
  comments: PublicComment[];
  totalElements: number;
  pageSize?: number;
  pageNumber?: number;
  error?: string;
  message?: string;
}

export interface CommentSubmitResult {
  success: boolean;
  commentId?: string;
  message: string;
}

export async function fetchDocumentComments(
  docId: number,
  page: number = 1,
): Promise<CommentsResponse> {
  const params = new URLSearchParams({ page: String(page) });
  const res = await fetch(`${API_BASE}/explore/${docId}/comments?${params}`);
  if (!res.ok) throw new Error(`Failed to load comments: ${res.status}`);
  return res.json();
}

export async function submitDocumentComment(
  docId: number,
  comment: string,
  name: string = "Anonymous",
  organization: string = "",
): Promise<CommentSubmitResult> {
  const params = new URLSearchParams({
    comment,
    name: name || "Anonymous",
  });
  if (organization) params.set("organization", organization);

  const res = await fetch(`${API_BASE}/explore/${docId}/comments?${params}`, {
    method: "POST",
  });
  return res.json();
}

export async function fetchExploreStats(): Promise<ExploreStats> {
  const res = await fetch(`${API_BASE}/explore/stats`);
  if (!res.ok) throw new Error(`Explore stats failed: ${res.status}`);
  return res.json();
}

// --- Admin API ---

function adminHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

export async function adminAuth(token: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/admin/auth`, {
      method: "POST",
      headers: adminHeaders(token),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export interface HostStats {
  loadAvg: [number, number, number] | null;
  cpuCount: number;
  memTotalBytes: number;
  memAvailableBytes: number;
  memUsedBytes: number;
  memUsedPct: number;
  cpuTempC: number | null;
  diskTotalBytes: number;
  diskUsedBytes: number;
  diskFreeBytes: number;
  diskUsedPct: number;
  uptimeSeconds: number | null;
  netRxBytes?: number;
  netTxBytes?: number;
}

export interface VectorCollectionStats {
  name: string;
  count: number;
  metadata?: Record<string, string>;
  sampleMetadataKeys?: string[];
}

export interface LearningStoreStats {
  totalEntries: number;
  bySource: Record<string, number>;
  byType: Record<string, number>;
  avgConfidence: number | null;
  confidenceDistribution: Record<string, number>;
  newestEntry: string | null;
  oldestEntry: string | null;
  error?: string;
}

export interface VectorDbStats {
  status: string;
  totalVectors?: number;
  sizeBytes?: number;
  collections?: VectorCollectionStats[];
  embeddingModel?: string;
  embeddingModelVersion?: string;
  embeddingDimensions?: number;
  learningStore?: LearningStoreStats;
  error?: string;
}

export interface AdminDashboard {
  system: {
    database: string;
    ollama: string;
    ollamaModel: string;
    ollamaUrl: string;
    dbSizeBytes: number;
    vectorDb?: VectorDbStats;
  };
  host?: HostStats;
  data: Record<string, number>;
  pipeline: {
    isRunning: boolean;
    nextScheduled: string | null;
    cronSchedule: string;
    totalRuns: number;
    successfulRuns: number;
    failedRuns: number;
    lastRun?: PipelineRunInfo;
  };
  llm: Record<string, unknown>;
}

export async function fetchAdminDashboard(token: string): Promise<AdminDashboard> {
  const res = await fetch(`${API_BASE}/admin/dashboard`, {
    headers: adminHeaders(token),
  });
  if (res.status === 401) throw new Error("Unauthorized");
  if (!res.ok) throw new Error(`Dashboard failed: ${res.status}`);
  return res.json();
}

export interface AdminPipelineStatus {
  isRunning: boolean;
  lastRun?: PipelineRunInfo;
}

export async function fetchAdminPipelineStatus(token: string): Promise<AdminPipelineStatus> {
  const res = await fetch(`${API_BASE}/admin/pipeline/status`, {
    headers: adminHeaders(token),
  });
  if (!res.ok) throw new Error(`Status failed: ${res.status}`);
  return res.json();
}

export async function fetchAdminPipelineHistory(token: string): Promise<PipelineRunInfo[]> {
  const res = await fetch(`${API_BASE}/admin/pipeline/history?limit=20`, {
    headers: adminHeaders(token),
  });
  if (!res.ok) throw new Error(`History failed: ${res.status}`);
  return res.json();
}

export async function triggerAdminPipeline(
  token: string,
  options?: { senator?: string; fetchOnly?: boolean },
): Promise<{ message: string }> {
  const params = new URLSearchParams();
  if (options?.senator) params.set("senator", options.senator);
  if (options?.fetchOnly) params.set("fetch_only", "true");
  const res = await fetch(`${API_BASE}/admin/pipeline/trigger?${params}`, {
    method: "POST",
    headers: adminHeaders(token),
  });
  if (res.status === 409) throw new Error("Pipeline is already running");
  if (!res.ok) throw new Error(`Trigger failed: ${res.status}`);
  return res.json();
}

export async function fetchAdminSystemStats(token: string): Promise<HostStats> {
  const res = await fetch(`${API_BASE}/admin/system/stats`, {
    headers: adminHeaders(token),
  });
  if (!res.ok) throw new Error(`System stats failed: ${res.status}`);
  return res.json();
}

export async function triggerAdminExplorePipeline(token: string): Promise<{ message: string }> {
  const res = await fetch(`${API_BASE}/admin/explore/trigger`, {
    method: "POST",
    headers: adminHeaders(token),
  });
  if (!res.ok) throw new Error(`Explore trigger failed: ${res.status}`);
  return res.json();
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
