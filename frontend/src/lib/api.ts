import { LeaderboardEntry, PaginatedVotes, Senator } from "@/types/senator";
import type { President, PresidentLeaderboardEntry } from "@/types/president";
import type { Justice, JusticeLeaderboardEntry } from "@/types/justice";
import type { ActionIssuesResponse, MyRepsResponse } from "@/types/action";
import type { PaginatedDocs, PoliticianCard, PoliticianProfile } from "@/types/politicians";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

const _fetchCache = new Map<string, { data: unknown; expiry: number }>();

async function cachedFetch<T>(url: string, ttlMs: number): Promise<T> {
  const now = Date.now();
  const hit = _fetchCache.get(url);
  if (hit && hit.expiry > now) return hit.data as T;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Fetch failed: ${res.status}`);
  const data: T = await res.json();
  _fetchCache.set(url, { data, expiry: now + ttlMs });
  if (_fetchCache.size > 100) {
    _fetchCache.forEach((entry, key) => {
      if (entry.expiry <= now) _fetchCache.delete(key);
    });
  }
  return data;
}

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
  return cachedFetch(`${API_BASE}/senators/states`, 120_000);
}

export async function fetchLeaderboard(): Promise<LeaderboardEntry[]> {
  return cachedFetch(`${API_BASE}/senators/leaderboard`, 120_000);
}

// --- House Representatives ---

export interface RepStateInfo {
  code: string;
  name: string;
  repCount: number;
}

export async function fetchRepStates(): Promise<RepStateInfo[]> {
  return cachedFetch(`${API_BASE}/representatives/states`, 120_000);
}

export interface PaginatedReps {
  entries: Senator[];
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
}

export async function fetchRepresentativesByState(
  state: string,
  page: number = 1,
  perPage: number = 10,
): Promise<PaginatedReps> {
  const res = await fetch(`${API_BASE}/representatives?state=${state}&page=${page}&per_page=${perPage}`);
  if (!res.ok) throw new Error(`Failed to load representatives: ${res.status}`);
  return res.json();
}

export async function fetchRepresentative(repId: string): Promise<Senator> {
  const res = await fetch(`${API_BASE}/representatives/${repId}`);
  if (!res.ok) throw new Error(`Representative not found: ${res.status}`);
  return res.json();
}

export interface PaginatedLeaderboard {
  entries: LeaderboardEntry[];
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
}

export async function fetchRepLeaderboard(
  page: number = 1,
  perPage: number = 50,
  party?: string,
): Promise<PaginatedLeaderboard> {
  const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
  if (party) params.set("party", party);
  const res = await fetch(`${API_BASE}/representatives/leaderboard?${params}`);
  if (!res.ok) throw new Error(`Failed to load house leaderboard: ${res.status}`);
  return res.json();
}

export async function fetchRepVotes(
  repId: string,
  options?: { category?: "recent" | "key"; page?: number; perPage?: number; filter?: string },
): Promise<PaginatedVotes> {
  const params = new URLSearchParams();
  if (options?.category) params.set("category", options.category);
  if (options?.page) params.set("page", String(options.page));
  if (options?.perPage) params.set("per_page", String(options.perPage));
  if (options?.filter) params.set("filter", options.filter);
  const res = await fetch(`${API_BASE}/representatives/${repId}/votes?${params}`);
  if (!res.ok) throw new Error(`Failed to load votes: ${res.status}`);
  return res.json();
}

export async function fetchSenatorVotes(
  senatorId: string,
  options?: { category?: "recent" | "key"; page?: number; perPage?: number; filter?: string },
): Promise<PaginatedVotes> {
  const params = new URLSearchParams();
  if (options?.category) params.set("category", options.category);
  if (options?.page) params.set("page", String(options.page));
  if (options?.perPage) params.set("per_page", String(options.perPage));
  if (options?.filter) params.set("filter", options.filter);
  const res = await fetch(`${API_BASE}/senators/${senatorId}/votes?${params}`);
  if (!res.ok) throw new Error(`Failed to load votes: ${res.status}`);
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
    promisePersistence: 0.20,
    independentVoting: 0.20,
    fundingDiversity: 0.15,
    legislativeEffectiveness: 0.20,
  },
  presidentScoreWeights: {
    independence: 0.15,
    followThrough: 0.20,
    publicMandate: 0.15,
    effectiveness: 0.20,
    competence: 0.15,
    agencyAlignment: 0.15,
  },
  industries: {},
  platformCategories: {},
  policyAreas: [],
};

let _cachedConfig: AppConfig | null = null;

export interface PipelineStepInfo {
  key: string;
  phase: string;
  label: string;
  status: "pending" | "active" | "done" | "skipped";
  detail?: string;
  total?: number;
  done?: number;
  startedAt?: string;
  completedAt?: string;
}

export interface PipelineRunInfo {
  id: number;
  pipelineType?: "senate" | "house";
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
  progressSteps?: PipelineStepInfo[] | null;
  // House-only fields
  repsProcessed?: number;
  repsTotal?: number;
  repsFailed?: number;
}

export interface HouseRunInfo {
  id: number;
  startedAt: string | null;
  completedAt: string | null;
  status: string;
  repsProcessed: number;
  repsTotal: number;
  repsFailed: number;
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

export async function fetchJusticeLeaderboard(): Promise<JusticeLeaderboardEntry[]> {
  const res = await fetch(`${API_BASE}/justices/leaderboard`);
  if (!res.ok) throw new Error(`Failed to load justice leaderboard: ${res.status}`);
  const raw = await res.json();
  return camelizeKeys(raw) as JusticeLeaderboardEntry[];
}

export async function fetchJustice(id: string): Promise<Justice> {
  const res = await fetch(`${API_BASE}/justices/${id}`);
  if (!res.ok) throw new Error(`Justice not found: ${res.status}`);
  const raw = await res.json();
  return camelizeKeys(raw) as Justice;
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
  options?: { docType?: string; chamber?: string; limit?: number; commentableOnly?: boolean; sort?: "relevance" | "date"; politicianId?: string },
): Promise<ExploreResponse> {
  const params = new URLSearchParams({ q: query });
  if (options?.docType) params.set("doc_type", options.docType);
  if (options?.chamber) params.set("chamber", options.chamber);
  if (options?.limit) params.set("limit", String(options.limit));
  if (options?.commentableOnly) params.set("commentable", "true");
  if (options?.sort) params.set("sort", options.sort);
  if (options?.politicianId) params.set("politician_id", options.politicianId);

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
  summary: string;
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
): Promise<ExploreDocumentSummary> {
  const res = await fetch(`${API_BASE}/explore/${id}/summary`, {
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

export interface UptimeInfo {
  processStartedAt: string | null;
  firstPipelineRun: string | null;
  totalRestarts: number;
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
  uptime?: UptimeInfo;
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

export interface ActionRefreshState {
  isRunning: boolean;
  stage: string | null;
  stageDetail: string | null;
  startedAt: string | null;
  lastCompletedAt: string | null;
  lastIssuesCreated: number;
  lastIssuesRetired: number;
  lastStoriesGenerated: number;
  lastBskyPosted: number;
  lastElapsed: number;
}

export interface AdminPipelineStatus {
  isRunning: boolean;
  houseIsRunning?: boolean;
  lastRun?: PipelineRunInfo;
  houseLastRun?: HouseRunInfo;
  actionRefresh?: ActionRefreshState;
}

export async function fetchAdminPipelineStatus(token: string): Promise<AdminPipelineStatus> {
  const res = await fetch(`${API_BASE}/admin/pipeline/status`, {
    headers: adminHeaders(token),
  });
  if (!res.ok) throw new Error(`Status failed: ${res.status}`);
  return res.json();
}

export async function clearStuckHousePipeline(token: string): Promise<{ cleared: number; message: string }> {
  const res = await fetch(`${API_BASE}/admin/pipeline/clear-stuck-house`, {
    method: "POST",
    headers: adminHeaders(token),
  });
  if (!res.ok) throw new Error(`Clear failed: ${res.status}`);
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

export async function triggerHousePipeline(token: string): Promise<{ message: string }> {
  const res = await fetch(`${API_BASE}/admin/pipeline/trigger-house`, {
    method: "POST",
    headers: adminHeaders(token),
  });
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

export interface VisitorStatsDay {
  date: string;
  uniqueVisitors: number;
}

export async function fetchAdminVisitorStats(
  token: string,
  days: number = 30,
): Promise<VisitorStatsDay[]> {
  const res = await fetch(`${API_BASE}/admin/visitor-stats?days=${days}`, {
    headers: adminHeaders(token),
  });
  if (!res.ok) throw new Error(`Visitor stats failed: ${res.status}`);
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


export async function fetchActionIssues(date?: string): Promise<ActionIssuesResponse> {
  const params = date ? `?date=${date}` : "";
  const res = await fetch(`${API_BASE}/action/issues${params}`);
  if (!res.ok) throw new Error(`Failed to load action issues: ${res.status}`);
  return res.json();
}

export async function submitPulseVote(
  issueId: number,
  stance: "concerned" | "not_priority",
): Promise<{ issueId: number; concernedCount: number; notPriorityCount: number }> {
  const res = await fetch(`${API_BASE}/action/pulse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ issue_id: issueId, stance }),
  });
  if (!res.ok) throw new Error(`Pulse vote failed: ${res.status}`);
  return res.json();
}

export async function fetchMyReps(state: string): Promise<MyRepsResponse> {
  return cachedFetch(`${API_BASE}/action/my-reps?state=${encodeURIComponent(state)}`, 300_000);
}

export interface ScoreSnapshot {
  date: string;
  overallScore: number;
  /** Scoring algorithm version that produced this snapshot (null for pre-v4.1 rows). */
  algorithmVersion?: string | null;
  scores: {
    fundingIndependence: number;
    promisePersistence: number;
    independentVoting: number;
    fundingDiversity: number;
    legislativeEffectiveness: number;
  };
}

export interface ScoreHistory {
  snapshots: ScoreSnapshot[];
}

export async function fetchSenatorHistory(senatorId: string): Promise<ScoreHistory> {
  return cachedFetch(`${API_BASE}/senators/${senatorId}/history`, 3_600_000);
}

export async function fetchRepresentativeHistory(repId: string): Promise<ScoreHistory> {
  return cachedFetch(`${API_BASE}/representatives/${repId}/history`, 3_600_000);
}

export interface OpenCommentItem {
  id: number;
  title: string;
  agencyName: string | null;
  commentsCloseOn: string;
  commentUrl: string;
  policyAreas: string[];
  docType: string;
  date: string;
  summary: string;
}

export async function fetchOpenComments(): Promise<OpenCommentItem[]> {
  return cachedFetch(`${API_BASE}/action/open-comments`, 3_600_000);
}

export interface BranchDocument {
  id: number;
  title: string;
  docType: string;
  date: string;
  url: string;
  chamber: string;
  summary: string;
  politicianName: string;
}

export interface BranchRecentResponse {
  branch: string;
  documents: BranchDocument[];
  count: number;
}

export async function fetchRecentByBranch(branch: string, limit = 15): Promise<BranchRecentResponse> {
  return cachedFetch(`${API_BASE}/action/recent/${branch}?limit=${limit}`, 120_000);
}

export interface CountryArticle {
  title: string;
  url: string;
  source: string;
  date: string;
}

export interface CountryNews {
  country: string;
  lat: number;
  lng: number;
  articleCount: number;
  articles: CountryArticle[];
}

export interface CountryNewsResponse {
  countries: CountryNews[];
}

export async function fetchCountryNews(): Promise<CountryNewsResponse> {
  const res = await fetch(`${API_BASE}/action/country-news`);
  if (!res.ok) throw new Error(`Failed to load country news: ${res.status}`);
  return res.json();
}

export interface ElectionSenator {
  id: string;
  name: string;
  state: string;
  party: string;
  overallScore: number;
  leadershipScore: number | null;
  yearsInOffice: number;
  upForElection: boolean;
}

export interface ElectionState {
  state: string;
  hasSenateRace: boolean;
  hasHouseRace: boolean;
  houseDistricts: number;
  senators: ElectionSenator[];
}

export interface ElectionInfo {
  nextElection: {
    date: string;
    type: string;
    year: number;
    daysUntil: number;
    isElectionDay: boolean;
    isElectionSeason: boolean;
  };
  senateSeatsUp: number;
  houseSeatsUp: number;
  states: ElectionState[];
}

export async function fetchElectionInfo(): Promise<ElectionInfo> {
  return cachedFetch(`${API_BASE}/action/elections`, 3_600_000);
}


export interface MonitorUpdate {
  id: number;
  date: string;
  summary: string;
  sourceUrl: string;
  sourceName: string;
  createdAt: string;
  articleTitle: string;
}

export interface NationalMonitor {
  id: number;
  slug: string;
  title: string;
  description: string;
  category: string;
  status: string;
  policyAreas: string[];
  createdAt: string;
  updatedAt: string;
  lastArticleDate: string | null;
  updateCount: number;
}

export interface NationalMonitorDetail extends NationalMonitor {
  updates: MonitorUpdate[];
}

export async function fetchMonitors(): Promise<{ monitors: NationalMonitor[] }> {
  return cachedFetch(`${API_BASE}/action/monitors`, 300_000);
}

export async function fetchMonitorDetail(slug: string): Promise<NationalMonitorDetail> {
  return cachedFetch(`${API_BASE}/action/monitors/${encodeURIComponent(slug)}`, 300_000);
}


export interface TimelineEntry {
  date: string;
  title: string;
  summary: string;
  policyAreas: string[];
  sourceUrl: string | null;
  sourceName: string | null;
  monitorSlug: string | null;
}

export interface TimelineWeek {
  weekNum: number;
  startDate: string;
  endDate: string;
  isCurrent: boolean;
  summary: string | null;
  topAreas: string[];
  entryCount: number;
  entries: TimelineEntry[];
}

export interface TimelineMonth {
  month: number;
  name: string;
  isCurrent: boolean;
  summary: string | null;
  topAreas: string[];
  entries: TimelineEntry[];
  weeks: TimelineWeek[];
  topThemes: [string, number][];
}

export interface UpcomingEvent {
  date: string;
  title: string;
  description: string;
  category: string;
  link: string;
  linkLabel: string;
}

export interface TimelineResponse {
  year: number;
  totalDays: number;
  currentMonth: number;
  currentWeekNum: number;
  topThemes: { area: string; count: number }[];
  monitors: { slug: string; title: string; status: string; updateCount: number }[];
  months: TimelineMonth[];
  upcomingEvents: UpcomingEvent[];
  yearSummary: { summary: string; topAreas: string[]; entryCount: number } | null;
}

export async function fetchTimeline(year?: number): Promise<TimelineResponse> {
  const params = year ? `?year=${year}` : "";
  return cachedFetch(`${API_BASE}/action/timeline${params}`, 300_000);
}

// ---------------------------------------------------------------------------
// Politicians directory
// ---------------------------------------------------------------------------

export async function fetchPoliticianDirectory(params?: {
  branch?: string;
  state?: string;
  party?: string;
  q?: string;
}): Promise<PoliticianCard[]> {
  const qs = new URLSearchParams();
  if (params?.branch) qs.set("branch", params.branch);
  if (params?.state) qs.set("state", params.state);
  if (params?.party) qs.set("party", params.party);
  if (params?.q) qs.set("q", params.q);
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return cachedFetch(`${API_BASE}/politicians${query}`, 120_000);
}

export async function fetchPoliticianProfile(id: string): Promise<PoliticianProfile> {
  return cachedFetch(`${API_BASE}/politicians/${encodeURIComponent(id)}`, 120_000);
}

export async function fetchPoliticianDocuments(
  id: string,
  page = 1,
  perPage = 20,
  docType?: string,
): Promise<PaginatedDocs> {
  const qs = new URLSearchParams({ page: String(page), per_page: String(perPage) });
  if (docType) qs.set("doc_type", docType);
  const res = await fetch(`${API_BASE}/politicians/${encodeURIComponent(id)}/documents?${qs}`);
  if (!res.ok) throw new Error(`Fetch failed: ${res.status}`);
  return res.json();
}
