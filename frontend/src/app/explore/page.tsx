"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import {
  searchExplore,
  fetchExploreStats,
  splitHighlights,
  type ExploreResult,
  type ExploreStats,
} from "@/lib/api";
import { localDateStr, formatUtcDate } from "@/lib/formatting";
import { chamberColor, chamberBg, chamberLabel } from "@/lib/chamber";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { useAsyncData } from "@/hooks/useAsyncData";
import { BOXED_CONTROL } from "@/lib/controlStyles";

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
  if (!result.commentUrl || !result.commentsCloseOn) return false;
  return result.commentsCloseOn >= localDateStr();
}

function daysUntilClose(closeDate: string): number {
  const close = new Date(closeDate + "T23:59:59");
  const now = new Date();
  return Math.max(0, Math.ceil((close.getTime() - now.getTime()) / 86_400_000));
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
    <p className="text-xs text-ink-lo leading-relaxed mb-3 line-clamp-3">
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
  const remaining = commentOpen ? daysUntilClose(result.commentsCloseOn) : 0;

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
              <span className="text-xs font-mono tracking-wide px-1.5 py-0.5 bg-phos/20 text-phos border border-phos/30 animate-pulse">
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
          SUBMIT YOUR COMMENT —{" "}
          {remaining === 0 ? "closes today" : `${remaining} day${remaining !== 1 ? "s" : ""} left`}
        </Link>
      )}
    </div>
  );
}

export default function ExplorePage() {
  return (
    <Suspense>
      <ExplorePageInner />
    </Suspense>
  );
}

/** A search as submitted — the four inputs that define one set of results. */
type SubmittedSearch = {
  query: string;
  chamber: ChamberFilter;
  commentableOnly: boolean;
  sort: "relevance" | "date";
};

const searchKey = (s: SubmittedSearch) =>
  `explore:${s.query}|${s.chamber}|${s.commentableOnly ? 1 : 0}|${s.sort}`;

const INDEX_BUILDING_MESSAGE =
  "The search index is still being built. This happens right after a data refresh — please check back in a few minutes.";

// Stable identity: a fresh [] on every render would restart every memo and
// effect downstream of `results` for no reason.
const EMPTY_RESULTS: ExploreResult[] = [];

function ExplorePageInner() {
  const searchParams = useSearchParams();
  // Seeded from ?q= at first render rather than assigned back in a mount
  // effect, so the input is right on the first paint instead of one frame late.
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [chamber, setChamber] = useState<ChamberFilter>("all");
  const [commentableOnly, setCommentableOnly] = useState(false);
  const [sortOrder, setSortOrder] = useState<"relevance" | "date">("relevance");
  const [stats, setStats] = useState<ExploreStats | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // The search that is currently on screen, as a value. A deep-linked ?q= is
  // just this state's initial value, which is why there is no mount effect
  // firing a search — and no window in which the URL says one thing and the
  // results pane shows another.
  const [submitted, setSubmitted] = useState<SubmittedSearch | null>(() => {
    const initialQ = searchParams.get("q")?.trim();
    return initialQ
      ? { query: initialQ, chamber: "all", commentableOnly: false, sort: "relevance" }
      : null;
  });

  useEffect(() => {
    fetchExploreStats()
      .then(setStats)
      .catch(() => {});
  }, []);

  const politicianId = searchParams.get("politician_id") || undefined;
  const request = useAsyncData(
    submitted ? `${searchKey(submitted)}|${politicianId ?? ""}` : "",
    submitted
      ? () =>
          searchExplore(submitted.query, {
            chamber: submitted.chamber === "all" ? undefined : submitted.chamber,
            limit: 30,
            commentableOnly: submitted.commentableOnly || undefined,
            sort: submitted.sort,
            politicianId,
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

  const runSearch = useCallback(
    (q: string, ch: ChamberFilter, commentOnly: boolean, sort: "relevance" | "date") => {
      const trimmed = q.trim();
      if (!trimmed) return;
      setSubmitted({ query: trimmed, chamber: ch, commentableOnly: commentOnly, sort });
    },
    []
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    runSearch(query, chamber, commentableOnly, sortOrder);
  };

  const handleSuggestion = (q: string) => {
    setQuery(q);
    runSearch(q, chamber, commentableOnly, sortOrder);
  };

  const handleChamberChange = (ch: ChamberFilter) => {
    setChamber(ch);
    if (searched && query.trim()) {
      runSearch(query, ch, commentableOnly, sortOrder);
    }
  };

  const handleCommentToggle = () => {
    const next = !commentableOnly;
    setCommentableOnly(next);
    if (searched && query.trim()) {
      runSearch(query, chamber, next, sortOrder);
    }
  };

  const handleSortChange = (s: "relevance" | "date") => {
    setSortOrder(s);
    if (searched && query.trim()) {
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
            eyebrow="Explore · search across every branch"
            title="Explore the record"
            aside={
              stats && stats.totalDocuments > 0 ? (
                <div className="text-right font-mono text-xs">
                  <p className="text-ink-lo tabular-nums">
                    {stats.totalDocuments.toLocaleString()} documents indexed
                  </p>
                  {stats.openForComment > 0 && (
                    <p className="text-signal-cyan tabular-nums">
                      {stats.openForComment} open for comment
                    </p>
                  )}
                </div>
              ) : null
            }
          >
            <p>
              Search any issue to see what all branches of government and federal agencies have done
              about it — by topic, or by exact name, number, or &ldquo;quoted phrase&rdquo;. Many
              regulatory documents are open for public comment — make your voice heard.
            </p>
          </PageMasthead>

          {/* Search form */}
          <form onSubmit={handleSubmit} className="mb-6">
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
                    disabled={loading || !query.trim()}
                    aria-busy={loading}
                    aria-label={loading ? "Searching" : "Search"}
                    className="text-xs font-mono tracking-widest text-signal-cyan hover:text-phos disabled:text-ink-min transition-colors shrink-0 px-2 py-1
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
                    chamber === f.value
                      ? BOXED_CONTROL.selected
                      : BOXED_CONTROL.unselected
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
          </div>

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

          {/* Error */}
          {error && (
            <div className="text-center py-8" role="alert">
              <p className="text-ink-lo text-base">{error}</p>
              <p className="text-ink-lo text-xs mt-2">
                The explore pipeline runs nightly. Try again later or trigger a pipeline run from
                the admin panel.
              </p>
            </div>
          )}

          {/* Loading */}
          {loading && (
            <div className="text-center py-12" role="status" aria-live="polite">
              <div className="inline-block border border-white/[0.07] px-6 py-3">
                <span className="text-ink-lo text-sm font-mono animate-pulse">
                  Searching government records...
                </span>
              </div>
            </div>
          )}

          {/* Partial-results notice */}
          {!loading && searched && semanticDown && results.length > 0 && (
            <div role="status" className="mb-4 px-3 py-2 border border-signal-amber/30 bg-signal-amber/5">
              <p className="text-signal-amber text-xs">
                Showing keyword matches only — the meaning-based index is rebuilding after a data
                refresh. Searches by topic will return more once it finishes, usually within a few
                minutes.
              </p>
            </div>
          )}

          {/* Results */}
          {!loading && searched && results.length > 0 && (
            <div aria-live="polite">
              {/* The results count is the section's heading, not a caption:
                  it is what the list below it is, and it keeps the document
                  outline going h1 -> h2 -> h3 instead of jumping to the
                  result titles. */}
              <h2 className="text-ink-lo text-xs mb-4 font-normal">
                {results.length} result{results.length !== 1 ? "s" : ""} for &ldquo;{resultsFor}
                &rdquo;
                {commentableOnly && (
                  <span className="text-phos-mid ml-2">— open for comment only</span>
                )}
                <span className="text-ink-min ml-2">
                  — sorted by {sortOrder === "date" ? "newest first" : "relevance"}
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
                No results found for &ldquo;{resultsFor}&rdquo;
              </p>
              <p className="text-ink-lo text-xs">
                Try a broader search term or adjust your filters.
              </p>
            </div>
          )}

          {/* Source attribution */}
          <div className="mt-12 text-center">
            <p className="text-ink-lo text-xs max-w-lg mx-auto">
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
