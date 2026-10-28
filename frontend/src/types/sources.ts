/** GET /api/sources (backend/app/api/sources.py): each source the record is
 *  built from, what it has supplied and how recent it is. Every figure and
 *  phrase is the backend's. */

export interface SourceRow {
  label: string;
  source: string;
  count: number;
  /** "1,591 roll calls, 119th Congress". */
  coverage: string;
  /** YYYY-MM-DD, or null when the source has nothing stored. */
  latest: string | null;
  /** What `latest` is: "newest record", or "last read" for a source whose
   *  records carry no date the site keeps. */
  dateLabel: string;
}

/** One source on the source map (GET /api/sources/health,
 *  backend/app/source_health.py). Every word and date is the backend's. */
export interface SourceHealthEntry {
  host: string;
  /** Who publishes it, as the source ledger names it. */
  publisher: string | null;
  /** What Civitas takes from it, in a line. */
  takes: string | null;
  state: "ok" | "refused" | "walled" | "unavailable" | "not_read";
  stateLabel: string;
  /** YYYY-MM-DD: failing since, for a source that can't be read now. */
  since: string | null;
  /** YYYY-MM-DD of the last read the source answered. */
  lastGood: string | null;
  /** The entry's one line, a template the backend filled. */
  line: string;
}

export interface SourceHealth {
  asOf: string;
  counts: Record<SourceHealthEntry["state"], number>;
  summary: string;
  sources: SourceHealthEntry[];
}

/** The admin's view adds the runs' internals. */
export interface AdminSourceHealthEntry extends SourceHealthEntry {
  /** What each reading module says it reads (its docstring). */
  reads: string[];
  /** The failure as recorded: a status code, a refusal's own words. */
  reason: string | null;
  lastAttempt: string | null;
  /** The same as the site prints it: "Oct 22, 2026". */
  lastAttemptText: string | null;
  /** The runs that read it, from the code: "Stock trades", "Election". */
  readBy: string[];
  /** "stock trades · senate_ptr": the runs and steps that read it. */
  runs: string[];
  requests30d: number;
  failedRequests30d: number;
  failedSteps30d: number;
  /** The three counts in words: "1 failed step; 2 of 3 requests failed". */
  failuresText: string;
  /** Modules that also send to it (alerts, posts), not reading it. */
  alsoSendsTo: string[];
}

/** A host Civitas only sends to (alerts, posts, forwarded feedback). */
export interface OutboundService {
  host: string;
  modules: string[];
}

export interface AdminSourceHealth extends Omit<SourceHealth, "sources"> {
  windowDays: number;
  sources: AdminSourceHealthEntry[];
  /** Hosts only sending modules name: not sources, listed apart. */
  outbound: OutboundService[];
}
