"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import PageFallback from "@/components/layout/PageFallback";
import BackToTop from "@/components/BackToTop";
import {
  searchExplore,
  fetchCommitteeRecordTotal,
  fetchExploreMatches,
  fetchExploreStats,
  splitHighlights,
  type ExploreResult,
  type ExploreStats,
} from "@/lib/api";
import { commentDaysLeft, formatUtcDate, isCommentPeriodOpen } from "@/lib/formatting";
import { chamberColor, chamberBg, chamberLabel } from "@/lib/chamber";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { useAsyncData } from "@/hooks/useAsyncData";
import { BOXED_CONTROL } from "@/lib/controlStyles";
import { billPageHref } from "@/lib/congress";
import MatchGroups from "@/components/explore/MatchGroups";

type ChamberFilter = "all" | "Senate" | "House" | "Executive" | "Judicial" | "Regulatory";

const CHAMBER_FILTERS: { label: string; value: ChamberFilter }[] = [
  { label: "All", value: "all" },
  { label: "Senate", value: "Senate" },
  { label: "House", value: "House" },
  { label: "Executive", value: "Executive" },
  { label: "Supreme Court", value: "Judicial" },
  { label: "Rulemaking", value: "Regulatory" },
];

// The topical list is unchanged — it is the only editorial surface on this
// page, and narrowing which issues get suggested is not a search change.
// The two exact-term examples are appended because search now runs a
// keyword channel alongside the semantic one, so document numbers and
// "quoted phrases" work, and this is the only place a visitor would find
// that out.
const SUGGESTED_QUERIES = [
  "tariffs and trade policy",
  "healthcare costs and prescription drugs",
  "immigration and border security",
  "climate change and clean energy",
  "gun control and second amendment",
  "technology regulation and AI",
  "student loan forgiveness",
  "Supreme Court constitutional rights",
  "Executive Order 14110",
  '"clean water act"',
];

function docTypeLabel(docType: string): string {
  return docType || "Document";
}

const formatDate = (dateStr: string): string =>
  formatUtcDate(dateStr, { year: "numeric", month: "short", day: "numeric" }, "en-US");

function isCommentOpen(result: ExploreResult): boolean {
  return !!result.commentUrl && isCommentPeriodOpen(result.commentsCloseOn);
}

/**
 * Render a backend excerpt with its matched query terms marked.
 *
 * The markers are control characters, not markup, so this builds React
 * nodes rather than reaching for `dangerouslySetInnerHTML` — the text is a
 * verbatim slice of a government document body and has no business being
 * parsed as HTML.
 */
function Snippet({ text }: { text: string }) {
  const segments = splitHighlights(text);
  if (segments.length === 0) return null;
  return (
    <p className="mb-3 line-clamp-3 font-sans text-xs leading-relaxed text-ink-lo">
      {segments.map((segment, i) =>
        segment.match ? (
          <mark key={i} className="bg-signal-cyan/20 text-signal-cyan px-0.5">
            {segment.text}
          </mark>
        ) : (
          <span key={i}>{segment.text}</span>
        )
      )}
    </p>
  );
}

function ResultCard({ result, query }: { result: ExploreResult; query: string }) {
  const detailHref = `/explore/${result.id}?q=${encodeURIComponent(query)}`;
  const commentOpen = isCommentOpen(result);
  const remaining = commentOpen ? commentDaysLeft(result.commentsCloseOn) : 0;

  return (
    <div className={`border transition-all ${chamberBg(result.chamber)}`}>
      <Link href={detailHref} className="block p-4 hover:brightness-125">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs font-mono tracking-wider ${chamberColor(result.chamber)}`}>
              {result.chamber === "Regulatory" && result.agencyName
                ? result.agencyName
                : chamberLabel(result.chamber)}
            </span>
            <span className="text-ink-min text-xs">|</span>
            <span className="text-ink-lo text-xs">{docTypeLabel(result.docType)}</span>
            {commentOpen && (
              // No pulse: the fading text dropped below 4.5:1 contrast for
              // part of every cycle (axe, WCAG 1.4.3), and an endless blink
              // on every open-comment result is motion nobody can pause.
              <span className="text-xs font-mono tracking-wide px-1.5 py-0.5 bg-phos/20 text-phos border border-phos/30">
                OPEN FOR COMMENT
              </span>
            )}
          </div>
          {result.date && (
            <span className="text-ink-min text-xs shrink-0">{formatDate(result.date)}</span>
          )}
        </div>

        <h3 className="text-sm text-ink-hi font-medium mb-2 leading-snug">{result.title}</h3>

        {/* The keyword channel returns an excerpt built around the matched
            terms, which says far more about why a document came back than
            its opening sentence does. Results only the semantic channel
            found have no matched terms, and fall back to the summary. */}
        <Snippet text={result.snippet || result.summary} />

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 flex-wrap">
            {result.politicianName && (
              <span className="text-xs text-ink-lo">{result.politicianName}</span>
            )}
            {(result.citedByCount ?? 0) > 0 && (
              <span
                className="text-xs font-mono tracking-wide text-ink-min"
                title="Other federal documents in this index that cite this one"
              >
                CITED BY {result.citedByCount}
              </span>
            )}
            {(result.duplicateCount ?? 0) > 0 && (
              <span
                className="text-xs font-mono tracking-wide text-ink-min"
                title="Near-identical copies of this document collapsed into this result"
              >
                +{result.duplicateCount} DUPLICATE
                {result.duplicateCount !== 1 ? "S" : ""}
              </span>
            )}
          </div>
          <span className="text-xs font-mono tracking-wide text-ink-lo">VIEW DETAILS →</span>
        </div>
      </Link>

      {commentOpen && (
        <Link
          href={detailHref + "#comment"}
          onClick={(e) => e.stopPropagation()}
          className="flex items-center justify-center gap-2 mx-3 mb-3 px-4 py-2 border border-signal-cyan/40 text-signal-cyan hover:text-phos
                     hover:border-signal-cyan/40 hover:bg-signal-cyan/10
                     text-xs font-mono tracking-wide transition-colors"
        >
          <span aria-hidden="true">✎</span>
          SUBMIT YOUR COMMENT ·{" "}
          {remaining === 0 ? "closes today" : `${remaining} day${remaining !== 1 ? "s" : ""} left`}
        </Link>
      )}
    </div>
  );
}

/** The index's counts beside the title. Both lines always take their
 * place, hidden until the counts arrive: on a phone they wrap under the
 * title, and appearing after the masthead had painted they pushed the
 * whole page down. */
function IndexCounts({ stats }: { stats: ExploreStats | null }) {
  const docs = stats?.totalDocuments ?? 0;
  const open = stats?.openForComment ?? 0;
  return (
    <div className="text-right font-mono text-xs">
      <p className={`text-ink-lo tabular-nums ${docs > 0 ? "" : "invisible"}`}>
        {docs.toLocaleString("en-US")} documents indexed
      </p>
      <p className={`text-signal-cyan tabular-nums ${docs > 0 && open > 0 ? "" : "invisible"}`}>
        {open} open for comment
      </p>
    </div>
  );
}

// The masthead's line, sent with the server's fallback too (PageFallback).
const EXPLORE_EYEBROW = "Explore · search across every branch";
const EXPLORE_TITLE = "Explore the record";
const EXPLORE_LEDE = (
  <p>
    Search any issue to see what all branches of government and federal agencies have done about it:
    by topic, or by exact name, number, or &ldquo;quoted phrase&rdquo;. Many regulatory documents
    are open for public comment: make your voice heard.
  </p>
);

export default function ExplorePage() {
  return (
    <Suspense
      fallback={
        <PageFallback
          eyebrow={EXPLORE_EYEBROW}
          title={EXPLORE_TITLE}
          lede={EXPLORE_LEDE}
          aside={<IndexCounts stats={null} />}
          width="max-w-4xl"
          mastheadClassName="mb-8"
          rows={4}
        />
      }
    >
      <ExplorePageInner />
    </Suspense>
  );
}

/** A search as submitted — the four inputs that define one set of results.
 * An empty query is a member's whole record (browse), only ever with a
 * politician filter. */
type SubmittedSearch = {
  query: string;
  chamber: ChamberFilter;
  commentableOnly: boolean;
  sort: "relevance" | "date";
};

const searchKey = (s: SubmittedSearch) =>
  `explore:${s.query}|${s.chamber}|${s.commentableOnly ? 1 : 0}|${s.sort}`;

const RESULT_LIMIT = 30;

const INDEX_BUILDING_MESSAGE =
  "The search index is still being built. This happens right after a data refresh: please check back in a few minutes.";

// Stable identity: a fresh [] on every render would restart every memo and
// effect downstream of `results` for no reason.
const EMPTY_RESULTS: ExploreResult[] = [];

function ExplorePageInner() {
  const searchParams = useSearchParams();
  // Seeded from ?q= at first render rather than assigned back in a mount
  // effect, so the input is right on the first paint instead of one frame late.
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [chamber, setChamber] = useState<ChamberFilter>("all");
  // ?comment=open: "every open comment period" (the homepage's and the
  // Action Center's link), which lists them with or without a query.
  const [commentableOnly, setCommentableOnly] = useState(
    () => searchParams.get("comment") === "open"
  );
  // A member's record with no query is listed newest first; the toggle says so.
  const [sortOrder, setSortOrder] = useState<"relevance" | "date">(() =>
    !searchParams.get("q")?.trim() && searchParams.get("politician_id") ? "date" : "relevance"
  );
  const [stats, setStats] = useState<ExploreStats | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // The search that is currently on screen, as a value. A deep-linked ?q= is
  // just this state's initial value, which is why there is no mount effect
  // firing a search — and no window in which the URL says one thing and the
  // results pane shows another.
  // Latched at mount, like the query: a profile's "view all documents" link
  // opens ?politician_id= with no query, which lists that member's record.
  const [politicianId, setPoliticianId] = useState<string | undefined>(
    () => searchParams.get("politician_id") || undefined
  );
  // ?agency=<slug>: an agency page's "all of its documents" link, and the
  // agency filter below. With no query, the agency's documents newest first.
  const [agency, setAgency] = useState<string | undefined>(
    () => searchParams.get("agency") || undefined
  );
  const [submitted, setSubmitted] = useState<SubmittedSearch | null>(() => {
    const initialQ = searchParams.get("q")?.trim();
    const openComments = searchParams.get("comment") === "open";
    if (initialQ) {
      return { query: initialQ, chamber: "all", commentableOnly: openComments, sort: "relevance" };
    }
    if (searchParams.get("politician_id") || searchParams.get("agency")) {
      return { query: "", chamber: "all", commentableOnly: openComments, sort: "date" };
    }
    return openComments
      ? { query: "", chamber: "all", commentableOnly: true, sort: "relevance" }
      : null;
  });

  useEffect(() => {
    fetchExploreStats()
      .then(setStats)
      .catch(() => {});
  }, []);

  const request = useAsyncData(
    submitted ? `${searchKey(submitted)}|${politicianId ?? ""}|${agency ?? ""}` : "",
    submitted
      ? () =>
          searchExplore(submitted.query, {
            chamber: submitted.chamber === "all" ? undefined : submitted.chamber,
            limit: RESULT_LIMIT,
            commentableOnly: submitted.commentableOnly || undefined,
            sort: submitted.sort,
            politicianId,
            agency,
          })
      : null
  );

  const searched = submitted !== null;
  const loading = request.loading;
  const resp = request.data;
  // An empty index is a backend state, not a transport failure, but it reaches
  // the reader the same way: as an explanation of why there is nothing here.
  const indexEmpty = Boolean(resp?.indexEmpty);
  const error = request.error ?? (indexEmpty ? INDEX_BUILDING_MESSAGE : "");
  const results = indexEmpty ? EMPTY_RESULTS : (resp?.results ?? EMPTY_RESULTS);
  // Results came from the keyword channel alone because the vector index is
  // rebuilding. A partial answer presented as a whole one is the thing to
  // avoid here — the reader has no other way to tell.
  const semanticDown = !indexEmpty && Boolean(resp?.semanticUnavailable);
  // What the results are *of*, which is not what is in the box: typing a new
  // term must not silently relabel the results still showing from the old one.
  const resultsFor = submitted?.query ?? "";
  const billHref = resultsFor ? billPageHref(resultsFor) : null;
  // Committee meetings and reports aren't in the index; how many the Daily
  // Digest has that match, offered beside the results.
  const committeeQuery = resultsFor.trim() && !politicianId ? resultsFor.trim() : "";
  const committee = useAsyncData(
    `committee:${committeeQuery}`,
    committeeQuery ? () => fetchCommitteeRecordTotal(committeeQuery) : null
  );
  // Members and bills aren't documents either: the ones the search names,
  // as their own groups above the results (never ranked among them).
  const matches = useAsyncData(
    `matches:${committeeQuery}`,
    committeeQuery ? () => fetchExploreMatches(committeeQuery) : null
  );
  // Whose record the results are limited to. The name comes from the results
  // themselves; the filter is shown either way.
  const politicianName = politicianId
    ? results.find((r) => r.politicianId === politicianId)?.politicianName || "one member"
    : "";
  const agencyOptions = stats?.agencies ?? [];
  const agencyName = agency
    ? agencyOptions.find((a) => a.slug === agency)?.name ||
      results.find((r) => r.agencySlug === agency)?.agencyName ||
      "one agency"
    : "";

  // The address names the search on screen, so a refresh or a shared link
  // shows the same thing. History API, not router.replace(): on this
  // statically prerendered route a same-route replace() is a no-op once the
  // page was opened with a query string (AGENTS.md, "Client-side URL state").
  const writeUrl = useCallback(
    (q: string, member: string | undefined, openComments: boolean, agencySlug?: string) => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (member) params.set("politician_id", member);
      if (agencySlug) params.set("agency", agencySlug);
      if (openComments) params.set("comment", "open");
      const search = params.toString();
      window.history.replaceState(null, "", search ? `?${search}` : window.location.pathname);
    },
    []
  );

  const runSearch = useCallback(
    (
      q: string,
      ch: ChamberFilter,
      commentOnly: boolean,
      sort: "relevance" | "date",
      agencySlug: string | undefined = agency
    ) => {
      const trimmed = q.trim();
      // An empty box lists the filtered member's or agency's record, or
      // every open comment period; with none of them it asks for nothing.
      if (!trimmed && !politicianId && !agencySlug && !commentOnly) return;
      setSubmitted({ query: trimmed, chamber: ch, commentableOnly: commentOnly, sort });
      writeUrl(trimmed, politicianId, commentOnly, agencySlug);
    },
    [politicianId, agency, writeUrl]
  );

  const clearPolitician = () => {
    setPoliticianId(undefined);
    const trimmed = submitted?.query ?? "";
    const openComments = submitted?.commentableOnly ?? false;
    writeUrl(trimmed, undefined, openComments, agency);
    // A browse has nothing left to show without its member, unless it is
    // the list of open comment periods.
    if (!trimmed && !openComments && !agency) setSubmitted(null);
  };

  const handleAgencyChange = (slug: string) => {
    const next = slug || undefined;
    setAgency(next);
    // An agency with an empty box lists its documents, newest first.
    const sort = !query.trim() && next ? "date" : sortOrder;
    setSortOrder(sort);
    if (query.trim() || politicianId || next || commentableOnly) {
      runSearch(query, chamber, commentableOnly, sort, next);
    } else {
      setSubmitted(null);
      writeUrl("", undefined, false);
    }
  };

  // On a phone the masthead, the box and the filters fill the first screen,
  // so the first result of a search sat a whole screen down (measured at
  // 1.00 viewport heights at 375 px). A search the reader starts brings the
  // box to the top instead: the filters stay in view below it and the
  // results start on the same screen. Only on a search they start, never on
  // load, which would fight the browser restoring a reader's place on Back.
  const formRef = useRef<HTMLFormElement>(null);
  const bringResultsUp = () => {
    if (!window.matchMedia("(max-width: 639px)").matches) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    formRef.current?.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    runSearch(query, chamber, commentableOnly, sortOrder);
    bringResultsUp();
  };

  const handleSuggestion = (q: string) => {
    setQuery(q);
    runSearch(q, chamber, commentableOnly, sortOrder);
    bringResultsUp();
  };

  const handleChamberChange = (ch: ChamberFilter) => {
    setChamber(ch);
    if (searched && (query.trim() || politicianId || agency || commentableOnly)) {
      runSearch(query, ch, commentableOnly, sortOrder);
    }
  };

  const handleCommentToggle = () => {
    const next = !commentableOnly;
    setCommentableOnly(next);
    // On with an empty box: every open comment period, closing soonest
    // first. Off again with nothing else asked: back to the empty page.
    if (query.trim() || politicianId || agency || next) {
      runSearch(query, chamber, next, sortOrder);
    } else {
      setSubmitted(null);
      writeUrl("", undefined, false);
    }
  };

  const handleSortChange = (s: "relevance" | "date") => {
    setSortOrder(s);
    if (searched && (query.trim() || politicianId || agency || commentableOnly)) {
      runSearch(query, chamber, commentableOnly, s);
    }
  };

  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-4xl mx-auto">
          {/* Header */}
          <PageMasthead
            className="mb-8"
            eyebrow={EXPLORE_EYEBROW}
            title={EXPLORE_TITLE}
            aside={<IndexCounts stats={stats} />}
          >
            {EXPLORE_LEDE}
          </PageMasthead>

          {/* Search form */}
          <form
            ref={formRef}
            onSubmit={handleSubmit}
            className="mb-6 scroll-mt-[var(--header-clearance)]"
          >
            <div className="panel">
              <TerminalTitlebar title="Query" />
              <div className="p-4">
                <label htmlFor="explore-search" className="sr-only">
                  Search government records
                </label>
                <div className="flex items-center gap-2">
                  <span className="text-ink-lo font-mono text-sm shrink-0" aria-hidden="true">
                    {">"}
                  </span>
                  <input
                    ref={inputRef}
                    id="explore-search"
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="What issue are you concerned about?"
                    className="flex-1 bg-transparent text-ink-hi text-sm font-mono placeholder:text-ink-min outline-none focus-visible:ring-1 focus-visible:ring-signal-cyan caret-phos"
                    autoFocus
                  />
                  <button
                    type="submit"
                    disabled={loading || (!query.trim() && !politicianId && !agency)}
                    aria-busy={loading}
                    aria-label={loading ? "Searching" : "Search"}
                    className="min-w-[7.5rem] text-xs font-mono tracking-widest text-signal-cyan hover:text-phos disabled:text-ink-min transition-colors shrink-0 px-2 py-1
                               border border-white/15 hover:border-signal-cyan/40 disabled:border-white/[0.07]
                               "
                  >
                    {loading ? "SEARCHING..." : "SEARCH"}
                  </button>
                </div>
              </div>
            </div>
          </form>

          {/* Filters */}
          <div className="mb-6 space-y-3">
            {/* Chamber / branch filters */}
            <div
              className="flex flex-wrap gap-2 justify-center"
              role="group"
              aria-label="Filter by branch"
            >
              {CHAMBER_FILTERS.map((f) => (
                <button
                  key={f.value}
                  onClick={() => handleChamberChange(f.value)}
                  aria-pressed={chamber === f.value}
                  className={`text-xs px-3 py-1  border transition-colors ${
                    chamber === f.value ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>

            {/* Sort + Commentable toggles */}
            <div className="flex justify-center items-center gap-3 flex-wrap">
              <div
                className="flex items-center border border-white/[0.07] overflow-hidden"
                role="group"
                aria-label="Sort order"
              >
                <button
                  onClick={() => handleSortChange("relevance")}
                  aria-pressed={sortOrder === "relevance"}
                  className={`text-xs px-3 py-1.5 transition-colors ${
                    sortOrder === "relevance"
                      ? "text-ink-hi bg-white/[0.03]"
                      : "text-ink-min hover:text-phos"
                  }`}
                >
                  Relevance
                </button>
                <span className="w-px h-4 bg-phos" />
                <button
                  onClick={() => handleSortChange("date")}
                  aria-pressed={sortOrder === "date"}
                  className={`text-xs px-3 py-1.5 transition-colors ${
                    sortOrder === "date"
                      ? "text-ink-hi bg-white/[0.03]"
                      : "text-ink-min hover:text-phos"
                  }`}
                >
                  Newest
                </button>
              </div>
              <button
                onClick={handleCommentToggle}
                aria-pressed={commentableOnly}
                aria-label="Show only documents open for public comment"
                className={`text-xs px-4 py-1.5  border transition-colors flex items-center gap-2 ${
                  commentableOnly
                    ? "border-phos/60 text-phos bg-phos/10"
                    : "border-white/[0.07] text-ink-lo hover:text-phos-mid hover:border-phos/30"
                }`}
              >
                {/* An indicator lamp: lit when the filter is on, dark when it
                    is off. Both arms briefly rendered the same green — the
                    palette migration turned an emerald/dim-green pair into
                    phos and a full-strength phos — so the dot stopped saying
                    anything. */}
                <span
                  className={`inline-block h-1.5 w-1.5 ${commentableOnly ? "bg-phos" : "bg-ink-min"}`}
                  aria-hidden="true"
                />
                Open for Public Comment
              </button>
            </div>

            {/* Agency: the Federal Register documents of one agency and its
                sub-agencies. Options are the agencies with documents here. */}
            {(agencyOptions.length > 0 || agency) && (
              <div className="flex justify-center">
                <label className="flex w-full max-w-md items-center gap-2 text-xs text-ink-lo">
                  <span className="shrink-0">Agency</span>
                  <select
                    value={agency ?? ""}
                    onChange={(e) => handleAgencyChange(e.target.value)}
                    className="min-h-8 w-full min-w-0 border border-white/[0.07] bg-surface-base px-2 text-xs text-ink-hi focus:border-signal-cyan/40"
                  >
                    <option value="">Every agency</option>
                    {agency && !agencyOptions.some((a) => a.slug === agency) && (
                      <option value={agency}>{agencyName}</option>
                    )}
                    {agencyOptions.map((a) => (
                      <option key={a.slug} value={a.slug}>
                        {"\u00a0\u00a0".repeat(a.depth)}
                        {a.name} ({a.documents.toLocaleString("en-US")})
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}
          </div>

          {politicianId && (
            <div
              role="status"
              className="mb-6 flex flex-wrap items-center justify-center gap-3 text-xs"
            >
              <span className="text-ink-lo">
                Only documents from <span className="text-ink-hi">{politicianName}</span>
              </span>
              <button
                type="button"
                onClick={clearPolitician}
                className="font-mono tracking-widest text-signal-cyan hover:text-phos transition-colors"
              >
                SEARCH EVERYONE
              </button>
            </div>
          )}

          {/* Suggested queries (shown before search) */}
          {!searched && (
            <div className="mb-8">
              <p className="text-ink-min text-xs text-center mb-3">Try searching for:</p>
              <div className="flex flex-wrap gap-2 justify-center">
                {SUGGESTED_QUERIES.map((q) => (
                  <button
                    key={q}
                    onClick={() => handleSuggestion(q)}
                    className="text-xs px-3 py-1.5 border border-white/[0.07]
                               text-ink-lo hover:text-phos hover:border-white/15
                               transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Error: an index still being built says so; a search that
              failed says that, and offers it again. */}
          {error && (
            <div className="text-center py-8" role="alert">
              {indexEmpty ? (
                <p className="text-ink-lo text-base">{error}</p>
              ) : (
                <p className="text-ink-lo text-base">
                  The search didn&apos;t answer, so nothing is shown for it yet.{" "}
                  <button
                    type="button"
                    onClick={() => {
                      // The button goes while the search runs; focus waits in the box.
                      inputRef.current?.focus({ preventScroll: true });
                      request.retry();
                    }}
                    className="font-mono text-xs tracking-widest text-signal-cyan underline underline-offset-2 hover:text-phos transition-colors"
                  >
                    TRY AGAIN
                  </button>
                </p>
              )}
            </div>
          )}

          {/* Loading */}
          {/* A screen tall, so what sits below the results (the source
              note) starts off screen and isn't shoved down when they land:
              that move was the page's layout shift on a browse link
              (CLS 0.04-0.06 at 1280 px, 2026-10-15). */}
          {loading && (
            <div className="min-h-screen text-center py-12" role="status" aria-live="polite">
              <div className="inline-block border border-white/[0.07] px-6 py-3">
                <span className="text-ink-lo text-sm font-mono pulse-subtle">
                  Searching government records…
                </span>
              </div>
            </div>
          )}

          {/* Partial-results notice */}
          {!loading && searched && semanticDown && results.length > 0 && (
            <div
              role="status"
              className="mb-4 px-3 py-2 border border-signal-amber/30 bg-signal-amber/5"
            >
              <p className="text-signal-amber text-xs">
                Showing keyword matches only: the meaning-based index is rebuilding after a data
                refresh. Searches by topic will return more once it finishes, usually within a few
                minutes.
              </p>
            </div>
          )}

          {/* The members and bills the search names. A bill number is
              among them: searched as text, it ranks documents that only
              share its digits above the bill. */}
          {!loading && searched && matches.data && (
            <MatchGroups matches={matches.data} query={committeeQuery} />
          )}

          {/* Without the groups (their request failed), a bill number still
              opens its page: it has the status, sponsor and every vote. */}
          {!loading && searched && billHref && matches.error && (
            <Link
              href={billHref}
              className="mb-4 block border border-phos/40 bg-phos/5 px-4 py-3 text-sm text-ink-hi hover:border-phos"
            >
              <span className="font-mono text-xs uppercase tracking-[0.12em] text-phos">Bill</span>{" "}
              Open the page for {resultsFor.trim()}: where it stands, who sponsored it, and how each
              member voted →
            </Link>
          )}

          {!loading && searched && committeeQuery && (committee.data ?? 0) > 0 && (
            <Link
              href={`/congress/committees?q=${encodeURIComponent(committeeQuery)}#committee-results`}
              className="mb-4 block border border-white/15 px-4 py-3 text-sm text-ink-hi hover:border-phos/40"
            >
              <span className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                Committees
              </span>{" "}
              {committee.data} committee{" "}
              {committee.data === 1 ? "meeting or report" : "meetings and reports"} in the Daily
              Digest match &ldquo;{committeeQuery}&rdquo; →
            </Link>
          )}

          {/* Results */}
          {!loading && searched && results.length > 0 && (
            <div aria-live="polite">
              {/* The results count is the section's heading, not a caption:
                  it is what the list below it is, and it keeps the document
                  outline going h1 -> h2 -> h3 instead of jumping to the
                  result titles. */}
              <h2 className="text-ink-lo text-xs mb-4 font-normal">
                {resultsFor ? (
                  <>
                    {results.length} result{results.length !== 1 ? "s" : ""} for &ldquo;
                    {resultsFor}&rdquo;
                  </>
                ) : politicianId ? (
                  <>
                    {results.length === RESULT_LIMIT ? "The most recent " : ""}
                    {results.length} document{results.length !== 1 ? "s" : ""} from {politicianName}
                  </>
                ) : agency && !submitted?.commentableOnly ? (
                  <>
                    {results.length === RESULT_LIMIT ? "The most recent " : ""}
                    {results.length} document{results.length !== 1 ? "s" : ""} from {agencyName}
                  </>
                ) : (
                  <>
                    {results.length === RESULT_LIMIT ? "The " : ""}
                    {results.length} open comment period{results.length !== 1 ? "s" : ""}
                    {results.length === RESULT_LIMIT ? " closing soonest" : ""}
                  </>
                )}
                {submitted?.commentableOnly && (resultsFor || politicianId) && (
                  <span className="text-phos-mid ml-2">· open for comment only</span>
                )}
                {agency && (resultsFor || politicianId || submitted?.commentableOnly) && (
                  <span className="text-ink-lo ml-2">· {agencyName} only</span>
                )}
                <span className="text-ink-min ml-2">
                  · sorted by{" "}
                  {!resultsFor && !politicianId && (!agency || submitted?.commentableOnly)
                    ? "closing date"
                    : !resultsFor || submitted?.sort === "date"
                      ? "newest first"
                      : "relevance"}
                </span>
              </h2>
              <div className="space-y-3">
                {results.map((r) => (
                  <ResultCard key={r.id} result={r} query={resultsFor} />
                ))}
              </div>
            </div>
          )}

          {/* No results */}
          {!loading && searched && !error && results.length === 0 && (
            <div className="text-center py-12">
              <p className="text-ink-lo text-base mb-2">
                {resultsFor ? (
                  <>No results found for &ldquo;{resultsFor}&rdquo;</>
                ) : politicianId ? (
                  <>No documents on record for this member</>
                ) : agency && !submitted?.commentableOnly ? (
                  <>No documents on record for this agency</>
                ) : (
                  <>No comment periods are open right now</>
                )}
              </p>
              <p className="text-ink-lo text-xs">
                Try a broader search term or adjust your filters.
              </p>
            </div>
          )}

          {/* Source attribution */}
          <div className="mt-12 text-center">
            <p className="mx-auto max-w-lg font-sans text-xs text-ink-lo">
              Data sourced from the Congressional Record (GovInfo API), the Federal Register
              (federalregister.gov), and Supreme Court opinions (supremecourt.gov via Oyez). Comment
              links go directly to regulations.gov.
            </p>
          </div>
        </div>
      </main>
      <Footer />
      <BackToTop />
    </>
  );
}
