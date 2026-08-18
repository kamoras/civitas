"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
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
          <mark key={i} className="bg-signal-cyan text-signal-cyan px-0.5">
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
              <span className="text-xs font-mono tracking-wide px-1.5 py-0.5 bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 animate-pulse">
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

function ExplorePageInner() {
  const searchParams = useSearchParams();
  const [query, setQuery] = useState("");
  const [chamber, setChamber] = useState<ChamberFilter>("all");
  const [commentableOnly, setCommentableOnly] = useState(false);
  const [sortOrder, setSortOrder] = useState<"relevance" | "date">("relevance");
  const [results, setResults] = useState<ExploreResult[]>([]);
  const [stats, setStats] = useState<ExploreStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState("");
  // Results came from the keyword channel alone because the vector index is
  // rebuilding. A partial answer presented as a whole one is the thing to
  // avoid here — the reader has no other way to tell.
  const [semanticDown, setSemanticDown] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchExploreStats()
      .then(setStats)
      .catch(() => {});
  }, []);

  const doSearch = useCallback(
    async (q: string, ch: ChamberFilter, commentOnly: boolean, sort: "relevance" | "date") => {
      if (!q.trim()) return;
      setLoading(true);
      setError("");
      setSearched(true);
      try {
        const politicianId = searchParams.get("politician_id") || undefined;
        const resp = await searchExplore(q, {
          chamber: ch === "all" ? undefined : ch,
          limit: 30,
          commentableOnly: commentOnly || undefined,
          sort,
          politicianId,
        });
        if (resp.indexEmpty) {
          setError(
            "The search index is still being built. This happens right after a data refresh — please check back in a few minutes."
          );
          setResults([]);
          setSemanticDown(false);
        } else {
          setResults(resp.results);
          setSemanticDown(Boolean(resp.semanticUnavailable));
        }
      } catch (e) {
        setError(
          e instanceof Error
            ? e.message
            : "Search failed. The explore pipeline may still be ingesting data."
        );
        setResults([]);
        setSemanticDown(false);
      } finally {
        setLoading(false);
      }
    },
    [searchParams]
  );

  useEffect(() => {
    const initialQ = searchParams.get("q");
    if (initialQ) {
      setQuery(initialQ);
      doSearch(initialQ, chamber, commentableOnly, sortOrder);
    }
    // Only run on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    doSearch(query, chamber, commentableOnly, sortOrder);
  };

  const handleSuggestion = (q: string) => {
    setQuery(q);
    doSearch(q, chamber, commentableOnly, sortOrder);
  };

  const handleChamberChange = (ch: ChamberFilter) => {
    setChamber(ch);
    if (searched && query.trim()) {
      doSearch(query, ch, commentableOnly, sortOrder);
    }
  };

  const handleCommentToggle = () => {
    const next = !commentableOnly;
    setCommentableOnly(next);
    if (searched && query.trim()) {
      doSearch(query, chamber, next, sortOrder);
    }
  };

  const handleSortChange = (s: "relevance" | "date") => {
    setSortOrder(s);
    if (searched && query.trim()) {
      doSearch(query, chamber, commentableOnly, s);
    }
  };

  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-4xl mx-auto">
          {/* Header */}
          <div className="mb-8 border-b-3 border-phos pb-5">
            <h1 className="font-display font-semibold text-xl sm:text-3xl text-ink-hi tracking-widest mb-2">
              EXPLORE
            </h1>
            <p className="text-ink-min text-base max-w-xl mx-auto">
              Search any issue to see what all branches of government and federal agencies have done
              about it — by topic, or by exact name, number, or &ldquo;quoted phrase&rdquo;. Many
              regulatory documents are open for public comment — make your voice heard.
            </p>
            {stats && stats.totalDocuments > 0 && (
              <div className="flex items-center justify-center gap-4 mt-2">
                <p className="text-ink-lo text-xs">
                  {stats.totalDocuments.toLocaleString()} documents indexed
                </p>
                {stats.openForComment > 0 && (
                  <p className="text-emerald-400/70 text-xs">
                    {stats.openForComment} open for comment
                  </p>
                )}
              </div>
            )}
          </div>

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
                      ? "border-phos/40 text-ink-hi bg-white/[0.03]"
                      : "border-white/[0.07] text-ink-lo hover:text-phos hover:border-white/15"
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
                    ? "border-emerald-500/60 text-emerald-400 bg-emerald-500/10"
                    : "border-white/[0.07] text-ink-lo hover:text-emerald-400/70 hover:border-emerald-500/30"
                }`}
              >
                <span
                  className={`inline-block w-1.5 h-1.5  ${
                    commentableOnly ? "bg-emerald-400" : "bg-phos"
                  }`}
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
            <div role="status" className="mb-4 px-3 py-2 border border-amber-500/30 bg-amber-500/5">
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
              <p className="text-ink-lo text-xs mb-4">
                {results.length} result{results.length !== 1 ? "s" : ""} for &ldquo;{query}&rdquo;
                {commentableOnly && (
                  <span className="text-emerald-400/70 ml-2">— open for comment only</span>
                )}
                <span className="text-ink-min ml-2">
                  — sorted by {sortOrder === "date" ? "newest first" : "relevance"}
                </span>
              </p>
              <div className="space-y-3">
                {results.map((r) => (
                  <ResultCard key={r.id} result={r} query={query} />
                ))}
              </div>
            </div>
          )}

          {/* No results */}
          {!loading && searched && !error && results.length === 0 && (
            <div className="text-center py-12">
              <p className="text-ink-lo text-base mb-2">
                No results found for &ldquo;{query}&rdquo;
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
