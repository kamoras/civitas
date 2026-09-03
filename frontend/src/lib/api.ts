import { LeaderboardEntry, PaginatedStockTrades, PaginatedVotes, Senator } from "@/types/senator";
import type { President, PresidentLeaderboardEntry } from "@/types/president";
import type { JusticeLeaderboardEntry } from "@/types/justice";
import type { ActionIssue, ActionIssuesResponse, MyRepsResponse } from "@/types/action";
import type { PoliticianCard } from "@/types/politicians";
import type { PaginatedBills } from "@/types/bill";
import type { GeocodeResult, PviMap, RaceSummary, TownBallot, TownEntry } from "@/types/election";
import type {
  JusticeScoreBreakdown,
  PresidentScoreBreakdown,
  RepresentationScoreBreakdown,
} from "@/types/scoreBreakdown";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

// Chamber selector for the two near-identical endpoints below (votes,
// stock trades) — an enum instead of a raw string so call sites can't pass
// an arbitrary/misspelled value. The enum is just the choice; its URL path
// segment is a separate concern, kept in CHAMBER_PATH below.
enum Chamber {
  Senate,
  House,
}

const CHAMBER_PATH: Record<Chamber, string> = {
  [Chamber.Senate]: "senators",
  [Chamber.House]: "representatives",
};

// Client-side cache TTLs for cachedFetch. Named so the intent (how volatile
// each endpoint is) is explicit and the same tier can't drift between callers.
const TTL = {
  /** Matches the backend's own _ACTION_ISSUES_CACHE_TTL_S — a client cache
   * outliving what the server itself promises as fresh just relocates the
   * staleness window (2026-08 incident: a 5-minute client cache held stale
   * Action Center data long after the backend's own header had been
   * shortened to fix exactly that). */
  VOLATILE: 30_000, // 30 sec
  /** Directory/leaderboard lists — refreshed a couple times per session. */
  SHORT: 120_000, // 2 min
  /** Deterministic derived data (score breakdowns, monitors) — changes at most daily. */
  MEDIUM: 300_000, // 5 min
  /** Rarely-changing reference data (score history, elections, open comments). */
  LONG: 3_600_000, // 1 hour
} as const;

// Single fetch-and-parse path for the many endpoints that share the exact
// "fetch → throw `<label>: <status>` on !ok → return JSON" shape. `camelize`
// runs the snake_case→camelCase conversion some endpoints need; `init` passes
// method/headers (e.g. admin auth). Endpoints with bespoke handling (404→null,
// !ok→[], a 401-specific message, returning res.ok as a boolean) intentionally
// do NOT use this and keep their own body.
async function requestJson<T>(
  url: string,
  errorLabel: string,
  opts?: { camelize?: boolean; init?: RequestInit }
): Promise<T> {
  const res = await fetch(url, opts?.init);
  if (!res.ok) throw new Error(`${errorLabel}: ${res.status}`);
  const data = await res.json();
  return (opts?.camelize ? camelizeKeys(data) : data) as T;
}

// ---------------------------------------------------------------------------
// Shape guarantees
//
// The backend can legitimately answer `{}` or `null` where the contract says
// "a list": an endpoint whose table the pipeline has not populated yet, a
// feature deployed ahead of its first run, a partial payload from a run that
// died halfway. Passed straight through, the crash surfaced at the caller's
// `.map` and took down the whole route — every one of these views already has
// a perfectly good "no data yet" state that never got the chance to render.
//
// These make the declared return types true, so the empty case reaches the UI
// as emptiness rather than as an exception.
// ---------------------------------------------------------------------------

/**
 * A shape correction, reported once per endpoint field per session.
 *
 * Coercing silently is right for the reader — the page renders its own
 * "no data yet" state instead of a crash — but wrong for whoever has to fix
 * the backend, who would otherwise see an idle-looking site and no signal at
 * all. Reported once per (endpoint, field) because several of these endpoints
 * are polled: a broken one would write a line every three seconds.
 */
const _reportedShapes = new Set<string>();

function reportShape(endpoint: string, field: string, expected: string, received: unknown): void {
  const key = `${endpoint} ${field}`;
  if (_reportedShapes.has(key)) return;
  _reportedShapes.add(key);
  const got = received === null ? "null" : Array.isArray(received) ? "array" : typeof received;
  console.warn(
    `[civitas] ${endpoint}: expected ${field} to be ${expected === "list" ? "an array" : "an object"}, got ${got}. ` +
      `Coerced to an empty ${expected} — the view will render its no-data state.`
  );
}

/** Test seam: forgets which corrections have already been reported. */
export function __resetShapeReports(): void {
  _reportedShapes.clear();
}

/** A payload that must be a list, as a list. */
function asList<T>(value: unknown, endpoint: string): T[] {
  if (Array.isArray(value)) return value;
  reportShape(endpoint, "the response", "list", value);
  return [];
}

/** A payload that must be an object, as an object. Reports only when asked to. */
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * An object payload whose named fields are guaranteed to be there and to have
 * the shape callers index into: `lists` come back as arrays, `records` as
 * objects. Fields the backend did send are left exactly as they arrived.
 */
function withShape<T extends object>(
  value: unknown,
  shape: { lists?: readonly (keyof T & string)[]; records?: readonly (keyof T & string)[] },
  endpoint: string
): T {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    reportShape(endpoint, "the response", "object", value);
  }
  const out = { ...asRecord(value) };
  for (const key of shape.lists ?? []) {
    if (!Array.isArray(out[key])) {
      reportShape(endpoint, `"${key}"`, "list", out[key]);
      out[key] = [];
    }
  }
  for (const key of shape.records ?? []) {
    if (!out[key] || typeof out[key] !== "object" || Array.isArray(out[key])) {
      reportShape(endpoint, `"${key}"`, "object", out[key]);
      out[key] = {};
    }
  }
  return out as T;
}

const _fetchCache = new Map<string, { data: unknown; expiry: number }>();
// In-flight requests keyed by URL. Concurrent callers of the same URL (e.g.
// the home preview, the Action Center parent, and IssuesTab all requesting
// /action/issues on mount) share a single network request instead of each
// firing their own — the resolved-data cache above can't dedupe these because
// they start before any of them has populated it. Entries are removed as soon
// as the request settles so a later call re-fetches once the TTL lapses, and a
// rejected request isn't cached (retries work).
const _inflight = new Map<string, Promise<unknown>>();

/** Test seam: drops both caches so one suite's stubbed fetch can't answer another's. */
export function __resetApiCache(): void {
  _fetchCache.clear();
  _inflight.clear();
}

async function cachedFetch<T>(url: string, ttlMs: number): Promise<T> {
  const now = Date.now();
  const hit = _fetchCache.get(url);
  if (hit && hit.expiry > now) return hit.data as T;

  const pending = _inflight.get(url);
  if (pending) return pending as Promise<T>;

  const request = (async () => {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Fetch failed: ${res.status}`);
    const data: T = await res.json();
    _fetchCache.set(url, { data, expiry: Date.now() + ttlMs });
    if (_fetchCache.size > 100) {
      const cutoff = Date.now();
      _fetchCache.forEach((entry, key) => {
        if (entry.expiry <= cutoff) _fetchCache.delete(key);
      });
    }
    return data;
  })();

  _inflight.set(url, request);
  try {
    return (await request) as T;
  } finally {
    _inflight.delete(url);
  }
}

export async function fetchSenatorsByState(state: string): Promise<Senator[]> {
  const url = `${API_BASE}/senators?state=${state}`;
  return asList(await requestJson(url, "Failed to load senators"), url);
}

export async function fetchSenator(senatorId: string): Promise<Senator> {
  return requestJson(`${API_BASE}/senators/${senatorId}`, "Senator not found");
}

export interface StateInfo {
  code: string;
  name: string;
  senatorCount: number;
}

export async function fetchStates(): Promise<StateInfo[]> {
  const url = `${API_BASE}/senators/states`;
  return asList(await cachedFetch(url, TTL.SHORT), url);
}

export async function fetchLeaderboard(): Promise<LeaderboardEntry[]> {
  const url = `${API_BASE}/senators/leaderboard`;
  return asList(await cachedFetch(url, TTL.SHORT), url);
}

// --- House Representatives ---

export interface RepStateInfo {
  code: string;
  name: string;
  repCount: number;
}

export async function fetchRepStates(): Promise<RepStateInfo[]> {
  const url = `${API_BASE}/representatives/states`;
  return asList(await cachedFetch(url, TTL.SHORT), url);
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
  perPage: number = 10
): Promise<PaginatedReps> {
  const url = `${API_BASE}/representatives?state=${state}&page=${page}&per_page=${perPage}`;
  return withShape<PaginatedReps>(
    await requestJson(url, "Failed to load representatives"),
    { lists: ["entries"] },
    url
  );
}

export async function fetchRepresentative(repId: string): Promise<Senator> {
  return requestJson(`${API_BASE}/representatives/${repId}`, "Representative not found");
}

// Score-breakdown ("show the math") panel data — lazy-fetched on first
// expand, cached via cachedFetch so re-toggling a panel doesn't refetch.
// 5-minute TTL: this is deterministic derived data that only changes once
// a day at most (the nightly pipeline run).

export async function fetchSenatorScoreBreakdown(
  senatorId: string
): Promise<RepresentationScoreBreakdown> {
  return cachedFetch(`${API_BASE}/senators/${senatorId}/score-breakdown`, TTL.MEDIUM);
}

export async function fetchRepScoreBreakdown(repId: string): Promise<RepresentationScoreBreakdown> {
  return cachedFetch(`${API_BASE}/representatives/${repId}/score-breakdown`, TTL.MEDIUM);
}

export async function fetchPresidentScoreBreakdown(id: string): Promise<PresidentScoreBreakdown> {
  const raw = await cachedFetch(`${API_BASE}/presidents/${id}/score-breakdown`, TTL.MEDIUM);
  return camelizeKeys(raw) as PresidentScoreBreakdown;
}

export async function fetchJusticeScoreBreakdown(id: string): Promise<JusticeScoreBreakdown> {
  const raw = await cachedFetch(`${API_BASE}/justices/${id}/score-breakdown`, TTL.MEDIUM);
  return camelizeKeys(raw) as JusticeScoreBreakdown;
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
  party?: string
): Promise<PaginatedLeaderboard> {
  const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
  if (party) params.set("party", party);
  const url = `${API_BASE}/representatives/leaderboard?${params}`;
  return withShape<PaginatedLeaderboard>(
    await requestJson(url, "Failed to load house leaderboard"),
    { lists: ["entries"] },
    url
  );
}

// Shared by the fetchRepVotes/fetchSenatorVotes and fetchRepStockTrades/
// fetchSenatorStockTrades pairs below — same query-building and fetch
// shape, differing only in the "representatives"/"senators" URL segment.
async function fetchPaginatedVotes(
  chamber: Chamber,
  entityId: string,
  options?: { category?: "recent" | "key"; page?: number; perPage?: number; filter?: string }
): Promise<PaginatedVotes> {
  const params = new URLSearchParams();
  if (options?.category) params.set("category", options.category);
  if (options?.page) params.set("page", String(options.page));
  if (options?.perPage) params.set("per_page", String(options.perPage));
  if (options?.filter) params.set("filter", options.filter);
  return requestJson(
    `${API_BASE}/${CHAMBER_PATH[chamber]}/${entityId}/votes?${params}`,
    "Failed to load votes"
  );
}

export async function fetchRepVotes(
  repId: string,
  options?: { category?: "recent" | "key"; page?: number; perPage?: number; filter?: string }
): Promise<PaginatedVotes> {
  return fetchPaginatedVotes(Chamber.House, repId, options);
}

export async function fetchSenatorVotes(
  senatorId: string,
  options?: { category?: "recent" | "key"; page?: number; perPage?: number; filter?: string }
): Promise<PaginatedVotes> {
  return fetchPaginatedVotes(Chamber.Senate, senatorId, options);
}

// Takes the URL segment directly rather than a Chamber, because the
// president is a fourth filer group and not a chamber — the disclosure
// endpoint is identical in shape (same OGE/PTR form fields, same 45-day
// deadline) but "Chamber.President" would be a lie. The three exported
// wrappers below keep call sites from passing an arbitrary segment.
async function fetchPaginatedStockTrades(
  pathSegment: string,
  entityId: string,
  options?: { page?: number; perPage?: number }
): Promise<PaginatedStockTrades> {
  const params = new URLSearchParams();
  if (options?.page) params.set("page", String(options.page));
  if (options?.perPage) params.set("per_page", String(options.perPage));
  return requestJson(
    `${API_BASE}/${pathSegment}/${entityId}/stock-trades?${params}`,
    "Failed to load stock trades"
  );
}

export async function fetchRepStockTrades(
  repId: string,
  options?: { page?: number; perPage?: number }
): Promise<PaginatedStockTrades> {
  return fetchPaginatedStockTrades(CHAMBER_PATH[Chamber.House], repId, options);
}

export async function fetchSenatorStockTrades(
  senatorId: string,
  options?: { page?: number; perPage?: number }
): Promise<PaginatedStockTrades> {
  return fetchPaginatedStockTrades(CHAMBER_PATH[Chamber.Senate], senatorId, options);
}

/** Disclosed buy/sell/exchange transactions from a president's OGE Form
 * 278-T filings — securities and virtual currency alike. Ranges as filed;
 * the form reports no profit figure and none is derived. */
export async function fetchPresidentStockTrades(
  presidentId: string,
  options?: { page?: number; perPage?: number }
): Promise<PaginatedStockTrades> {
  return fetchPaginatedStockTrades("presidents", presidentId, options);
}

export async function fetchBillsInFlight(options?: {
  stage?: string;
  chamber?: "senate" | "house";
  party?: "D" | "R" | "I";
  q?: string;
  sort?: "recent" | "hot" | "stale";
  page?: number;
  perPage?: number;
}): Promise<PaginatedBills> {
  const params = new URLSearchParams();
  if (options?.stage) params.set("stage", options.stage);
  if (options?.chamber) params.set("chamber", options.chamber);
  if (options?.party) params.set("party", options.party);
  if (options?.q) params.set("q", options.q);
  if (options?.sort) params.set("sort", options.sort);
  if (options?.page) params.set("page", String(options.page));
  if (options?.perPage) params.set("per_page", String(options.perPage));
  // cachedFetch, not requestJson: the bills page's funnel, stage groups,
  // and list views can request the same URL concurrently (and again on
  // mode/filter toggles) — share one network request and reuse it for the
  // same 2 minutes the backend's Cache-Control already promises.
  const url = `${API_BASE}/bills?${params}`;
  return withShape<PaginatedBills>(
    await cachedFetch(url, TTL.SHORT),
    {
      lists: ["bills"],
      // The stage funnel reduces over these on every render; an absent map has
      // to arrive as {} rather than undefined.
      records: ["stageCounts"],
    },
    url
  );
}

async function fetchHighlights(chamber: Chamber, entityId: string): Promise<string[]> {
  const res = await fetch(`${API_BASE}/${CHAMBER_PATH[chamber]}/${entityId}/highlights`);
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data.highlights) ? data.highlights : [];
}

export async function fetchSenatorHighlights(senatorId: string): Promise<string[]> {
  return fetchHighlights(Chamber.Senate, senatorId);
}

export async function fetchRepHighlights(repId: string): Promise<string[]> {
  return fetchHighlights(Chamber.House, repId);
}

export interface IndustryInfo {
  name: string;
  color: string;
}

export interface BillStageInfo {
  name: string;
  color: string;
  order: number;
}

export interface AppConfig {
  industries: Record<string, IndustryInfo>;
  platformCategories: Record<string, string>;
  policyAreas: string[];
  billStages: Record<string, BillStageInfo>;
}

const DEFAULT_CONFIG: AppConfig = {
  industries: {},
  platformCategories: {},
  policyAreas: [],
  billStages: {
    INTRODUCED: { name: "Introduced", color: "#6b7280", order: 1 },
    REFERRED: { name: "Referred to Committee", color: "#60a5fa", order: 2 },
    IN_COMMITTEE: { name: "In Committee", color: "#3b82f6", order: 3 },
    PASSED_CHAMBER: { name: "Passed Chamber", color: "#8b5cf6", order: 4 },
    IN_OTHER_CHAMBER: { name: "In Other Chamber", color: "#f59e0b", order: 5 },
    TO_PRESIDENT: { name: "To President", color: "#ec4899", order: 6 },
    ENACTED: { name: "Enacted", color: "#00ff41", order: 7 },
    VETOED: { name: "Vetoed", color: "#ef4444", order: 8 },
  },
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

/**
 * Every pipeline the backend records runs for (see `_history_entry` in
 * `app/api/admin.py` — this list must match the labels passed there).
 *
 * Exported as a named type on purpose: anything that branches per pipeline
 * type should key an exhaustive `Record<PipelineType, …>` off this, so adding
 * a sixth pipeline is a compile error at every branch instead of silently
 * falling through to whichever type happens to be the default. Election runs
 * shipped as "SENATE" rows in the admin run history for exactly that reason.
 */
export type PipelineType = "senate" | "house" | "stock_trades" | "supplementary" | "election";

export interface PipelineRunInfo {
  id: number;
  pipelineType?: PipelineType;
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
  // Stock-trades-only fields
  houseTradesIngested?: number;
  senateTradesIngested?: number;
  presidentTradesIngested?: number;
  // Supplementary-only fields
  exploreDocsIngested?: number;
  justicesScored?: number;
  justicesSkipped?: boolean;
  presidentsUpdated?: number;
  // Election-only fields
  candidatesSynced?: number;
  financialsRefreshed?: number;
  coverageItemsIngested?: number;
}

/**
 * A row from `GET /admin/pipeline_history`, which interleaves runs of every
 * pipeline type.
 *
 * The senate-shaped counters above are declared required because
 * `PipelineStatus.lastRun` — the Senate pipeline's own status object — always
 * carries them. History rows do not: `_history_entry` only attaches each
 * type's own `extra` fields, so a House or Election row genuinely has no
 * `senatorsProcessed` and no `cacheHits` at runtime. Typing them as required
 * there was a lie that let `r.cacheHits + r.cacheMisses` (NaN) type-check
 * clean. This alias tells the truth for the mixed feed.
 */
type SenateOnlyRunFields =
  | "currentPhase"
  | "senatorsProcessed"
  | "senatorsTotal"
  | "senatorsFailed"
  | "billsClassified"
  | "llmCalls"
  | "cacheHits"
  | "cacheMisses";

export type PipelineHistoryRun = Omit<PipelineRunInfo, SenateOnlyRunFields> &
  Partial<Pick<PipelineRunInfo, SenateOnlyRunFields>>;

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
  progressSteps?: PipelineStepInfo[] | null;
}

export interface StockTradesRunInfo {
  id: number;
  startedAt: string | null;
  completedAt: string | null;
  status: string;
  houseTradesIngested: number;
  senateTradesIngested: number;
  presidentTradesIngested: number;
  elapsedSeconds: number | null;
  errorMessage: string | null;
  progressSteps?: PipelineStepInfo[] | null;
}

export interface SupplementaryRunInfo {
  id: number;
  startedAt: string | null;
  completedAt: string | null;
  status: string;
  currentPhase: string | null;
  exploreDocsIngested: number;
  justicesScored: number;
  justicesSkipped: boolean;
  presidentsUpdated: number;
  elapsedSeconds: number | null;
  errorMessage: string | null;
  progressSteps?: PipelineStepInfo[] | null;
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
      ])
    );
  }
  return obj;
}

export async function fetchPresidentLeaderboard(): Promise<PresidentLeaderboardEntry[]> {
  // No camelize: the endpoint already serializes camelCase via model_dump(
  // by_alias=True). Recursively re-camelizing is a no-op on scalar fields but
  // would corrupt any data-keyed map field the moment one is added — the
  // justice /justices/{id} response once carried `agreementMatrix`, a map
  // keyed by justice IDs ("sonia_sotomayor"); recursively camelizing
  // rewrote those keys to "soniaSotomayor", rendered as a run-together
  // "SoniaSotomayor" wherever the ID was split on "_".
  const url = `${API_BASE}/presidents/leaderboard`;
  return asList(await requestJson(url, "Failed to load president leaderboard"), url);
}

/** Null when no president is currently serving (excluded from
 * /presidents/leaderboard — see the backend's get_president_leaderboard
 * docstring) — a real, if brief, possible state (e.g. a same-day
 * transition), not an error. */
export async function fetchCurrentPresident(): Promise<President | null> {
  const res = await fetch(`${API_BASE}/presidents/current`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to load current president: ${res.status}`);
  // No camelizeKeys: the endpoint serializes camelCase via model_dump(
  // by_alias=True) — see fetchPresidentLeaderboard's comment for why
  // re-camelizing is a data-keyed-map landmine.
  return (await res.json()) as President;
}

export async function fetchJusticeLeaderboard(): Promise<JusticeLeaderboardEntry[]> {
  const url = `${API_BASE}/justices/leaderboard`;
  return asList(
    await requestJson(url, "Failed to load justice leaderboard", {
      camelize: true,
    }),
    url
  );
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
  // Cosine distance from the semantic channel. Null when only the keyword
  // channel matched this document — those results have no vector distance,
  // and coercing one would invent a number.
  distance: number | null;
  // Keyword-in-context excerpt. Matched terms are wrapped in the U+0002 /
  // U+0003 control characters (never markup — see EXPLORE_HIGHLIGHT_* and
  // splitHighlights below). Falls back to the document summary for results
  // only the semantic channel found, which have no matched terms to mark.
  snippet: string;
  // The three fields below are optional only because a proxy-cached
  // response written before this feature deployed can still be served for
  // its max-age. Every live response carries them.
  //
  // Which retrieval channels returned this document: "semantic", "keyword",
  // or both.
  matchedBy?: ("semantic" | "keyword")[];
  // Inbound citations from other federal documents in the index — the raw
  // count behind the PageRank authority signal.
  citedByCount?: number;
  // Near-identical copies of this document collapsed into this result.
  duplicateCount?: number;
  url: string;
  summary: string;
  agencyName: string;
  commentUrl: string;
  commentsCloseOn: string;
}

/** Sentinels the backend wraps matched query terms in. See splitHighlights. */
export const EXPLORE_HIGHLIGHT_START = "\u0002";
export const EXPLORE_HIGHLIGHT_END = "\u0003";

/**
 * Split a snippet into plain and matched segments for rendering.
 *
 * The backend marks matches with control characters rather than `<b>` tags
 * precisely so this never needs `dangerouslySetInnerHTML`: a Federal
 * Register body can contain anything, and a highlighted excerpt is
 * attacker-adjacent text going straight into a page.
 */
export function splitHighlights(snippet: string): { text: string; match: boolean }[] {
  if (!snippet) return [];

  // Scan once, emitting a segment at every marker. Both sentinels are
  // consumed as delimiters and never reach a segment's text: a snippet
  // truncated mid-highlight can leave one unpaired, and an unpaired marker
  // left in place would render as an invisible control character inside the
  // excerpt.
  const segments: { text: string; match: boolean }[] = [];
  let buffer = "";
  let matching = false;

  const flush = () => {
    if (buffer) segments.push({ text: buffer, match: matching });
    buffer = "";
  };

  for (const char of snippet) {
    if (char === EXPLORE_HIGHLIGHT_START) {
      flush();
      matching = true;
    } else if (char === EXPLORE_HIGHLIGHT_END) {
      flush();
      matching = false;
    } else {
      buffer += char;
    }
  }
  flush();
  return segments;
}

export interface ExploreResponse {
  query: string;
  results: ExploreResult[];
  count: number;
  // How many documents each retrieval channel returned before fusion.
  // Useful for spotting a dead channel. Optional for the same
  // proxy-cache reason as the per-result fields above.
  channels?: { semantic: number; keyword: number };
  // Set when these results came from the keyword channel alone because the
  // vector index is missing or mid-rebuild. Deliberately not the same as
  // `channels.semantic === 0`: a filtered query can retrieve zero vectors
  // from a perfectly healthy index.
  semanticUnavailable?: boolean;
  // Set when the vector index doesn't exist yet (e.g. right after an admin
  // reset, before the next pipeline run). The backend returns HTTP 503 with
  // this flag so the UI can show an honest "still indexing" state instead of
  // "no results found" while the stats header still claims thousands of docs.
  indexEmpty?: boolean;
}

export interface ExploreStats {
  totalDocuments: number;
  byType: Record<string, number>;
  byChamber: Record<string, number>;
  openForComment: number;
}

export async function searchExplore(
  query: string,
  options?: {
    docType?: string;
    chamber?: string;
    limit?: number;
    commentableOnly?: boolean;
    sort?: "relevance" | "date";
    politicianId?: string;
  }
): Promise<ExploreResponse> {
  const params = new URLSearchParams({ q: query });
  if (options?.docType) params.set("doc_type", options.docType);
  if (options?.chamber) params.set("chamber", options.chamber);
  if (options?.limit) params.set("limit", String(options.limit));
  if (options?.commentableOnly) params.set("commentable", "true");
  if (options?.sort) params.set("sort", options.sort);
  if (options?.politicianId) params.set("politician_id", options.politicianId);

  // Can't route through requestJson: it throws on any !res.ok and discards
  // the body, which would turn the 503 "index still building" contract into a
  // bare "Explore search failed: 503" and never let the page show the
  // friendlier indexing state.
  const res = await fetch(`${API_BASE}/explore?${params}`);
  if (res.status === 503) {
    let body: { indexEmpty?: boolean } = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON 503 (proxy/gateway) — fall through to the generic error */
    }
    if (body?.indexEmpty) {
      return { query, results: [], count: 0, indexEmpty: true };
    }
    throw new Error("Explore search failed: 503");
  }
  if (!res.ok) throw new Error(`Explore search failed: ${res.status}`);
  return (await res.json()) as ExploreResponse;
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

// Mirrors backend/app/pipeline/analyze/prompts.py's parse_explore_document_summary —
// same two markers, same split logic — so the frontend can re-derive
// {summary, keyPoints, impact} from the raw accumulated stream text after
// every chunk, not just once the stream completes.
const KEY_POINTS_MARKER = "KEY POINTS:";
const IMPACT_MARKER = "IMPACT:";

export function parseExploreSummaryText(text: string): ExploreDocumentSummary {
  const [summaryPart, rest = ""] = splitOnce(text, KEY_POINTS_MARKER);
  const [keyPointsPart, impactPart = ""] = splitOnce(rest, IMPACT_MARKER);

  const summary = summaryPart.replace(/^SUMMARY:/, "").trim();
  const keyPoints = keyPointsPart
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("-"))
    .map((line) => line.replace(/^-+/, "").trim());

  return { summary, keyPoints, impact: impactPart.trim() };
}

function splitOnce(text: string, marker: string): [string, string?] {
  const i = text.indexOf(marker);
  if (i === -1) return [text, undefined];
  return [text.slice(0, i), text.slice(i + marker.length)];
}

export async function fetchExploreDocument(id: number): Promise<ExploreDocumentDetail> {
  return requestJson(`${API_BASE}/explore/${id}`, "Document not found");
}

// Reads the SSE stream from POST /explore/:id/summary — one JSON object
// per `data:` line, either {delta: "<chunk>"} while generating or the
// terminal {done: true, summary, keyPoints, impact} (cache hits send only
// the terminal event, no deltas). onDelta fires with the raw text
// accumulated SO FAR after every chunk, letting the caller re-derive
// {summary, keyPoints, impact} from partial text as it streams in —
// this file only forwards bytes, it doesn't parse the marker format.
export async function streamExploreDocumentSummary(
  id: number,
  onDelta: (fullTextSoFar: string) => void
): Promise<ExploreDocumentSummary> {
  const res = await fetch(`${API_BASE}/explore/${id}/summary`, { method: "POST" });
  if (!res.ok || !res.body) throw new Error(`Summary failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullText = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() || ""; // last element may be a partial event — keep it for next read

    for (const event of events) {
      const line = event.trim();
      if (!line.startsWith("data:")) continue;
      const parsed = JSON.parse(line.slice("data:".length).trim());
      if (parsed.done) {
        return {
          summary: parsed.summary ?? "",
          keyPoints: parsed.keyPoints ?? [],
          impact: parsed.impact ?? "",
        };
      }
      if (typeof parsed.delta === "string") {
        fullText += parsed.delta;
        onDelta(fullText);
      }
    }
  }

  throw new Error("Summary stream ended without a final result");
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
  page: number = 1
): Promise<CommentsResponse> {
  const params = new URLSearchParams({ page: String(page) });
  return requestJson(`${API_BASE}/explore/${docId}/comments?${params}`, "Failed to load comments");
}

export async function submitDocumentComment(
  docId: number,
  comment: string,
  name: string = "Anonymous",
  organization: string = ""
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
  return requestJson(`${API_BASE}/explore/stats`, "Explore stats failed");
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
  /** Model the SEARCH index was built with (sqlite-vec migration,
   * 2026-07) — distinct from embeddingModel, which is the
   * classification-side model. Empty until the first reindex completes. */
  indexModelVersion?: string;
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
  stockTradesIsRunning?: boolean;
  supplementaryIsRunning?: boolean;
  electionIsRunning?: boolean;
  lastRun?: PipelineRunInfo;
  houseLastRun?: HouseRunInfo;
  stockTradesLastRun?: StockTradesRunInfo;
  supplementaryLastRun?: SupplementaryRunInfo;
  // PipelineHistoryRun (not the full PipelineRunInfo): an election run
  // carries candidatesSynced/financialsRefreshed/coverageItemsIngested/
  // progressSteps but never the Senate-only fields (senatorsProcessed,
  // cacheHits, ...) PipelineRunInfo marks required — same reasoning
  // PipelineHistoryRun's own comment gives for the mixed history feed.
  // This field existed on the backend response and was typed for the
  // shared history table, but never had its own live-progress section
  // here (2026-09 gap: the confirmed-candidates step's per-state detail,
  // and the weekly source crawler's findings, were invisible outside the
  // server log).
  electionLastRun?: PipelineHistoryRun;
  actionRefresh?: ActionRefreshState;
}

export async function fetchAdminPipelineStatus(token: string): Promise<AdminPipelineStatus> {
  return requestJson(`${API_BASE}/admin/pipeline/status`, "Status failed", {
    init: { headers: adminHeaders(token) },
  });
}

export async function clearStuckHousePipeline(
  token: string
): Promise<{ cleared: number; message: string }> {
  return requestJson(`${API_BASE}/admin/pipeline/clear-stuck-house`, "Clear failed", {
    init: { method: "POST", headers: adminHeaders(token) },
  });
}

export async function clearStuckSupplementaryPipeline(
  token: string
): Promise<{ cleared: number; message: string }> {
  return requestJson(`${API_BASE}/admin/pipeline/clear-stuck-supplementary`, "Clear failed", {
    init: { method: "POST", headers: adminHeaders(token) },
  });
}

export async function clearStuckStockTradesPipeline(
  token: string
): Promise<{ cleared: number; message: string }> {
  return requestJson(`${API_BASE}/admin/pipeline/clear-stuck-stock-trades`, "Clear failed", {
    init: { method: "POST", headers: adminHeaders(token) },
  });
}

export async function fetchAdminPipelineHistory(token: string): Promise<PipelineHistoryRun[]> {
  const url = `${API_BASE}/admin/pipeline/history?limit=20`;
  return asList(
    await requestJson(url, "History failed", {
      init: { headers: adminHeaders(token) },
    }),
    url
  );
}

export async function fetchAdminSystemStats(token: string): Promise<HostStats> {
  return requestJson(`${API_BASE}/admin/system/stats`, "System stats failed", {
    init: { headers: adminHeaders(token) },
  });
}

export interface VisitorStatsDay {
  date: string;
  uniqueVisitors: number;
}

export async function fetchAdminVisitorStats(
  token: string,
  days: number = 30
): Promise<VisitorStatsDay[]> {
  return requestJson(`${API_BASE}/admin/visitor-stats?days=${days}`, "Visitor stats failed", {
    init: { headers: adminHeaders(token) },
  });
}

export interface VisitorBreakdownEntry {
  name: string;
  count: number;
}

export interface VisitorBreakdown {
  date: string;
  browsers: VisitorBreakdownEntry[];
  os: VisitorBreakdownEntry[];
  devices: VisitorBreakdownEntry[];
}

export async function fetchAdminVisitorBreakdown(token: string): Promise<VisitorBreakdown> {
  return requestJson(`${API_BASE}/admin/visitor-breakdown`, "Visitor breakdown failed", {
    init: { headers: adminHeaders(token) },
  });
}

export interface TopPageEntry {
  path: string;
  views: number;
}

export async function fetchAdminTopPages(token: string, days: number = 7): Promise<TopPageEntry[]> {
  const url = `${API_BASE}/admin/top-pages?days=${days}`;
  return asList(
    await requestJson(url, "Top pages failed", {
      init: { headers: adminHeaders(token) },
    }),
    url
  );
}

export interface PhaseTimingPhase {
  phase: string;
  seconds: number;
  pct: number;
  steps: number;
}

export interface PhaseTimingStep {
  stepKey: string;
  label: string;
  phase: string;
  status: string;
  seconds: number | null;
  blockedSeconds: number;
}

export interface RateLimitSource {
  source: string;
  requests: number;
  blockedSeconds: number;
}

export interface PhaseTimingRun {
  runId: number;
  startedAt: string | null;
  completedAt: string | null;
  totalSeconds: number;
  untimedSteps: number;
  blockedSeconds: number;
  blockedPct: number;
  rateLimitSources: RateLimitSource[];
  phases: PhaseTimingPhase[];
  steps: PhaseTimingStep[];
}

export interface PipelineTimings {
  kind: string;
  pipelineType: string;
  runs: PhaseTimingRun[];
  phaseTrend: Record<string, { runId: number; seconds: number }[]>;
}

export async function fetchAdminPipelineTimings(
  token: string,
  kind: string = "pipeline_runs",
  runs: number = 10
): Promise<PipelineTimings> {
  return requestJson(
    `${API_BASE}/admin/pipeline/timings?kind=${encodeURIComponent(kind)}&runs=${runs}`,
    "Pipeline timings failed",
    { init: { headers: adminHeaders(token) } }
  );
}

export interface VacancyResult {
  id: string;
  name: string;
  isCurrent: boolean;
  vacancyReason: string | null;
  leftOfficeDate: string | null;
}

export async function setPoliticianVacancy(
  token: string,
  politicianId: string,
  isCurrent: boolean,
  reason?: string,
  leftOfficeDate?: string
): Promise<VacancyResult> {
  const params = new URLSearchParams({ is_current: String(isCurrent) });
  if (reason) params.set("reason", reason);
  if (leftOfficeDate) params.set("left_office_date", leftOfficeDate);
  const res = await fetch(
    `${API_BASE}/admin/politicians/${encodeURIComponent(politicianId)}/vacancy?${params}`,
    { method: "POST", headers: adminHeaders(token) }
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Vacancy update failed: ${res.status}`);
  }
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
  // Cached + de-duped like the other Action Center endpoints, at the same
  // TTL as the backend's own Cache-Control header (see TTL.VOLATILE) —
  // collapses the several consumers that request the same day's issues on
  // mount without outliving what the server itself promises as fresh.
  const url = `${API_BASE}/action/issues${params}`;
  return withShape<ActionIssuesResponse>(
    await cachedFetch(url, TTL.VOLATILE),
    { lists: ["issues", "availableDates"] },
    url
  );
}

/** Most recently touched issues regardless of is_current — backs the
 * homepage's "record index", which needs entries that don't vanish the
 * instant an issue retires (see backend's get_recent_action_issues). */
export async function fetchRecentActionIssues(limit = 10): Promise<{ issues: ActionIssue[] }> {
  const url = `${API_BASE}/action/issues/recent?limit=${limit}`;
  return withShape<{ issues: ActionIssue[] }>(
    await cachedFetch(url, TTL.VOLATILE),
    { lists: ["issues"] },
    url
  );
}

export async function submitPulseVote(
  issueId: number,
  stance: "concerned" | "not_priority"
): Promise<{ issueId: number; concernedCount: number; notPriorityCount: number }> {
  return requestJson(`${API_BASE}/action/pulse`, "Pulse vote failed", {
    init: {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ issue_id: issueId, stance }),
    },
  });
}

export async function fetchMyReps(state: string): Promise<MyRepsResponse> {
  const url = `${API_BASE}/action/my-reps?state=${encodeURIComponent(state)}`;
  return withShape<MyRepsResponse>(
    await cachedFetch(url, TTL.MEDIUM),
    { lists: ["senators", "representatives"] },
    url
  );
}

export interface ScoreSnapshot {
  date: string;
  overallScore: number;
  /** Scoring algorithm version that produced this snapshot (null for pre-v4.1 rows). */
  algorithmVersion?: string | null;
  /** Dimension name -> score. Keys differ by entity type (senator/rep:
   * fundingIndependence/promisePersistence/independentVoting/
   * fundingDiversity/legislativeEffectiveness; president: publicMandate/
   * effectiveness/agencyAlignment/historicalLegacy) — untyped here since
   * ScoreTrend (the only consumer) only ever reads date/overallScore. */
  scores: Record<string, number>;
}

export interface ScoreHistory {
  snapshots: ScoreSnapshot[];
}

export async function fetchSenatorHistory(senatorId: string): Promise<ScoreHistory> {
  const url = `${API_BASE}/senators/${senatorId}/history`;
  return withShape<ScoreHistory>(await cachedFetch(url, TTL.LONG), { lists: ["snapshots"] }, url);
}

export async function fetchRepresentativeHistory(repId: string): Promise<ScoreHistory> {
  const url = `${API_BASE}/representatives/${repId}/history`;
  return withShape<ScoreHistory>(await cachedFetch(url, TTL.LONG), { lists: ["snapshots"] }, url);
}

export async function fetchPresidentHistory(presidentId: string): Promise<ScoreHistory> {
  const url = `${API_BASE}/presidents/${presidentId}/history`;
  return withShape<ScoreHistory>(await cachedFetch(url, TTL.LONG), { lists: ["snapshots"] }, url);
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
  const url = `${API_BASE}/action/open-comments`;
  return asList(await cachedFetch(url, TTL.LONG), url);
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
  const url = `${API_BASE}/action/country-news`;
  return withShape<CountryNewsResponse>(
    await requestJson(url, "Failed to load country news"),
    { lists: ["countries"] },
    url
  );
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
  const url = `${API_BASE}/action/elections`;
  return withShape<ElectionInfo>(
    await cachedFetch(url, TTL.LONG),
    {
      lists: ["states"],
    },
    url
  );
}

// ── Midterm-elections feature (candidate rosters, race detail, PVI) ──
// Separate namespace from /action/elections above (that endpoint is the
// lightweight Action Center teaser; this is the fuller candidate-research
// feature) — see backend/app/api/elections.py. Race detail is fetched
// server-side by app/elections/[raceId]/page.tsx, not through this client.

export async function fetchRaces(): Promise<RaceSummary[]> {
  const url = `${API_BASE}/elections/races`;
  return asList(await cachedFetch(url, TTL.SHORT), url);
}

export async function fetchPviMap(): Promise<PviMap> {
  // `states` and `districts` are maps, not lists, and callers index into them
  // directly — an absent one has to arrive as {} rather than undefined.
  const raw = asRecord(await cachedFetch(`${API_BASE}/elections/pvi`, TTL.LONG));
  return {
    ...raw,
    states: asRecord(raw.states),
    districts: asRecord(raw.districts),
  } as unknown as PviMap;
}

/** Not cached — every address is a distinct, user-entered, one-off
 * lookup; caching would just hold addresses in memory for no benefit. */
export async function fetchDistrictForAddress(address: string): Promise<GeocodeResult> {
  const res = await fetch(`${API_BASE}/elections/geocode?address=${encodeURIComponent(address)}`);
  if (!res.ok) throw new Error(`Failed to resolve address: ${res.status}`);
  return res.json();
}

/** The curated town list for a state — empty when the town-lookup feature
 * isn't configured or no town has been added for this state yet. Empty
 * is a normal, expected response, not an error; the UI hides the town
 * selector rather than showing one with nothing in it. */
export async function fetchTownsForState(state: string): Promise<TownEntry[]> {
  const data = await cachedFetch<{ towns: TownEntry[] }>(
    `${API_BASE}/elections/states/${encodeURIComponent(state)}/towns`,
    TTL.LONG,
  );
  return data.towns;
}

/** One curated town's local ballot content — see TownBallot's docstring
 * for the tri-state `status`. Deliberately TTL.SHORT, not the LONG tier
 * this would otherwise fall into as "reference data": local races and
 * measures are certified and struck continuously through a cycle, same
 * as the statewide ones on the ballot page above. */
export async function fetchTownBallot(state: string, town: string): Promise<TownBallot> {
  return cachedFetch(
    `${API_BASE}/elections/states/${encodeURIComponent(state)}/towns/${encodeURIComponent(town)}/ballot`,
    TTL.SHORT,
  );
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
  const url = `${API_BASE}/action/monitors`;
  return withShape<{ monitors: NationalMonitor[] }>(
    await cachedFetch(url, TTL.MEDIUM),
    { lists: ["monitors"] },
    url
  );
}

export async function fetchMonitorDetail(slug: string): Promise<NationalMonitorDetail> {
  return cachedFetch(`${API_BASE}/action/monitors/${encodeURIComponent(slug)}`, TTL.MEDIUM);
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
  const url = `${API_BASE}/action/timeline${params}`;
  return withShape<TimelineResponse>(
    await cachedFetch(url, TTL.MEDIUM),
    { lists: ["months", "monitors", "topThemes", "upcomingEvents"] },
    url
  );
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
  const url = `${API_BASE}/politicians${query}`;
  return asList(await cachedFetch(url, TTL.SHORT), url);
}

export interface FeedbackSubmission {
  category: "bug" | "idea" | "accessibility" | "data" | "other";
  message: string;
  pageUrl?: string;
}

export async function submitFeedback(
  submission: FeedbackSubmission
): Promise<{ ok: boolean; issueUrl: string | null }> {
  const res = await fetch(`${API_BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      category: submission.category,
      message: submission.message,
      page_url: submission.pageUrl,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to submit feedback: ${res.status}`);
  }
  const data = await res.json();
  return { ok: data.ok, issueUrl: data.issueUrl ?? data.issue_url ?? null };
}
