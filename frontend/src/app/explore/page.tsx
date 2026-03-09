"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import {
  searchExplore,
  fetchExploreStats,
  type ExploreResult,
  type ExploreStats,
} from "@/lib/api";

type ChamberFilter = "all" | "Senate" | "House" | "Executive" | "Judicial" | "Regulatory";

const CHAMBER_FILTERS: { label: string; value: ChamberFilter }[] = [
  { label: "All", value: "all" },
  { label: "Senate", value: "Senate" },
  { label: "House", value: "House" },
  { label: "Executive", value: "Executive" },
  { label: "Supreme Court", value: "Judicial" },
  { label: "Rulemaking", value: "Regulatory" },
];

const SUGGESTED_QUERIES = [
  "tariffs and trade policy",
  "healthcare costs and prescription drugs",
  "immigration and border security",
  "climate change and clean energy",
  "gun control and second amendment",
  "technology regulation and AI",
  "student loan forgiveness",
  "Supreme Court constitutional rights",
];

function chamberColor(chamber: string): string {
  if (chamber === "Senate") return "text-neon-cyan";
  if (chamber === "House") return "text-neon-pink";
  if (chamber === "Executive") return "text-neon-yellow";
  if (chamber === "Judicial") return "text-purple-400";
  if (chamber === "Regulatory") return "text-orange-400";
  return "text-matrix-green/60";
}

function chamberBg(chamber: string): string {
  if (chamber === "Senate") return "border-neon-cyan/30 bg-neon-cyan/5";
  if (chamber === "House") return "border-neon-pink/30 bg-neon-pink/5";
  if (chamber === "Executive") return "border-neon-yellow/30 bg-neon-yellow/5";
  if (chamber === "Judicial") return "border-purple-400/30 bg-purple-400/5";
  if (chamber === "Regulatory") return "border-orange-400/30 bg-orange-400/5";
  return "border-matrix-green/20 bg-matrix-green/5";
}

function chamberLabel(chamber: string): string {
  if (chamber === "Regulatory") return "AGENCY";
  return chamber?.toUpperCase() || "GOV";
}

function docTypeLabel(docType: string): string {
  return docType || "Document";
}

function formatDate(dateStr: string): string {
  if (!dateStr) return "";
  try {
    const d = new Date(dateStr + "T00:00:00");
    return d.toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function isCommentOpen(result: ExploreResult): boolean {
  if (!result.commentUrl || !result.commentsCloseOn) return false;
  return result.commentsCloseOn >= new Date().toISOString().slice(0, 10);
}

function daysUntilClose(closeDate: string): number {
  const close = new Date(closeDate + "T23:59:59");
  const now = new Date();
  return Math.max(0, Math.ceil((close.getTime() - now.getTime()) / 86_400_000));
}

function ResultCard({
  result,
  query,
}: {
  result: ExploreResult;
  query: string;
}) {
  const detailHref = `/explore/${result.id}?q=${encodeURIComponent(query)}`;
  const commentOpen = isCommentOpen(result);
  const remaining = commentOpen ? daysUntilClose(result.commentsCloseOn) : 0;

  return (
    <div className={`border rounded transition-all ${chamberBg(result.chamber)}`}>
      <Link href={detailHref} className="block p-4 hover:brightness-125">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`text-[10px] font-pixel tracking-wider ${chamberColor(result.chamber)}`}
            >
              {result.chamber === "Regulatory" && result.agencyName
                ? result.agencyName
                : chamberLabel(result.chamber)}
            </span>
            <span className="text-matrix-green/30 text-xs">|</span>
            <span className="text-matrix-green/50 text-xs">
              {docTypeLabel(result.docType)}
            </span>
            {commentOpen && (
              <span className="text-[10px] font-pixel px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 animate-pulse">
                OPEN FOR COMMENT
              </span>
            )}
          </div>
          {result.date && (
            <span className="text-matrix-green/40 text-xs shrink-0">
              {formatDate(result.date)}
            </span>
          )}
        </div>

        <h3 className="text-sm text-matrix-green font-medium mb-2 leading-snug">
          {result.title}
        </h3>

        {(result.summary || result.snippet) && (
          <p className="text-xs text-matrix-green/50 leading-relaxed mb-3 line-clamp-3">
            {result.summary || result.snippet}
          </p>
        )}

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 flex-wrap">
            {result.politicianName && (
              <span className="text-xs text-matrix-green/60">
                {result.politicianName}
              </span>
            )}
          </div>
          <span className="text-[10px] font-pixel text-neon-cyan/50">
            VIEW DETAILS →
          </span>
        </div>
      </Link>

      {commentOpen && (
        <div className="px-4 pb-3 pt-0 flex items-center justify-between gap-3 border-t border-emerald-500/15">
          <span className="text-[10px] text-emerald-400/70">
            {remaining === 0
              ? "Comments close today!"
              : `${remaining} day${remaining !== 1 ? "s" : ""} left to comment`}
          </span>
          <a
            href={result.commentUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="text-[10px] font-pixel px-3 py-1.5 rounded
                       bg-emerald-500/20 text-emerald-400 border border-emerald-500/40
                       hover:bg-emerald-500/30 hover:border-emerald-500/60
                       transition-colors shrink-0"
          >
            SUBMIT COMMENT →
          </a>
        </div>
      )}
    </div>
  );
}

export default function ExplorePage() {
  const [query, setQuery] = useState("");
  const [chamber, setChamber] = useState<ChamberFilter>("all");
  const [commentableOnly, setCommentableOnly] = useState(false);
  const [sortOrder, setSortOrder] = useState<"relevance" | "date">("relevance");
  const [results, setResults] = useState<ExploreResult[]>([]);
  const [stats, setStats] = useState<ExploreStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState("");
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
        const resp = await searchExplore(q, {
          chamber: ch === "all" ? undefined : ch,
          limit: 30,
          commentableOnly: commentOnly || undefined,
          sort,
        });
        setResults(resp.results);
      } catch (e) {
        setError(
          e instanceof Error ? e.message : "Search failed. The explore pipeline may still be ingesting data.",
        );
        setResults([]);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

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
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-4xl mx-auto">
          {/* Header */}
          <div className="text-center mb-8">
            <h1 className="font-pixel text-xl sm:text-3xl text-matrix-green tracking-widest mb-2">
              EXPLORE
            </h1>
            <p className="text-matrix-green/40 text-sm max-w-xl mx-auto">
              Search any issue to see what all branches of government and federal
              agencies have done about it. Many regulatory documents are open for
              public comment — make your voice heard.
            </p>
            {stats && stats.totalDocuments > 0 && (
              <div className="flex items-center justify-center gap-4 mt-2">
                <p className="text-matrix-green/50 text-xs">
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
            <div className="terminal-window">
              <div className="terminal-titlebar" aria-hidden="true">
                <span className="terminal-dot red" />
                <span className="terminal-dot yellow" />
                <span className="terminal-dot green" />
                <span className="ml-3 text-white/40 text-xs font-terminal">
                  query.sh
                </span>
              </div>
              <div className="p-4">
                <label htmlFor="explore-search" className="sr-only">
                  Search government records
                </label>
                <div className="flex items-center gap-2">
                  <span className="text-matrix-green/60 font-terminal text-sm shrink-0" aria-hidden="true">
                    {">"}
                  </span>
                  <input
                    ref={inputRef}
                    id="explore-search"
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="What issue are you concerned about?"
                    className="flex-1 bg-transparent text-matrix-green text-sm font-terminal
                               placeholder:text-matrix-green/40 outline-none focus-visible:ring-1 focus-visible:ring-neon-cyan/50 caret-matrix-green"
                    autoFocus
                  />
                  <button
                    type="submit"
                    disabled={loading || !query.trim()}
                    className="text-[10px] font-pixel text-neon-cyan/70 hover:text-neon-cyan
                               disabled:text-matrix-green/20 transition-colors shrink-0 px-2 py-1
                               border border-neon-cyan/30 hover:border-neon-cyan/60 disabled:border-matrix-green/10
                               rounded"
                  >
                    {loading ? "[SEARCHING...]" : "[SEARCH]"}
                  </button>
                </div>
              </div>
            </div>
          </form>

          {/* Filters */}
          <div className="mb-6 space-y-3">
            {/* Chamber / branch filters */}
            <div className="flex flex-wrap gap-2 justify-center" role="group" aria-label="Filter by branch">
              {CHAMBER_FILTERS.map((f) => (
                <button
                  key={f.value}
                  onClick={() => handleChamberChange(f.value)}
                  aria-pressed={chamber === f.value}
                  className={`text-xs px-3 py-1 rounded border transition-colors ${
                    chamber === f.value
                      ? "border-matrix-green/60 text-matrix-green bg-matrix-green/10"
                      : "border-matrix-green/15 text-matrix-green/50 hover:text-matrix-green/60 hover:border-matrix-green/30"
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>

            {/* Sort + Commentable toggles */}
            <div className="flex justify-center items-center gap-3 flex-wrap">
              <div className="flex items-center rounded border border-matrix-green/15 overflow-hidden" role="group" aria-label="Sort order">
                <button
                  onClick={() => handleSortChange("relevance")}
                  aria-pressed={sortOrder === "relevance"}
                  className={`text-xs px-3 py-1.5 transition-colors ${
                    sortOrder === "relevance"
                      ? "text-matrix-green bg-matrix-green/10"
                      : "text-matrix-green/40 hover:text-matrix-green/60"
                  }`}
                >
                  Relevance
                </button>
                <span className="w-px h-4 bg-matrix-green/15" />
                <button
                  onClick={() => handleSortChange("date")}
                  aria-pressed={sortOrder === "date"}
                  className={`text-xs px-3 py-1.5 transition-colors ${
                    sortOrder === "date"
                      ? "text-matrix-green bg-matrix-green/10"
                      : "text-matrix-green/40 hover:text-matrix-green/60"
                  }`}
                >
                  Newest
                </button>
              </div>
              <button
                onClick={handleCommentToggle}
                aria-pressed={commentableOnly}
                aria-label="Show only documents open for public comment"
                className={`text-xs px-4 py-1.5 rounded border transition-colors flex items-center gap-2 ${
                  commentableOnly
                    ? "border-emerald-500/60 text-emerald-400 bg-emerald-500/10"
                    : "border-matrix-green/15 text-matrix-green/50 hover:text-emerald-400/70 hover:border-emerald-500/30"
                }`}
              >
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                  commentableOnly ? "bg-emerald-400" : "bg-matrix-green/30"
                }`} aria-hidden="true" />
                Open for Public Comment
              </button>
            </div>
          </div>

          {/* Suggested queries (shown before search) */}
          {!searched && (
            <div className="mb-8">
              <p className="text-matrix-green/30 text-xs text-center mb-3">
                Try searching for:
              </p>
              <div className="flex flex-wrap gap-2 justify-center">
                {SUGGESTED_QUERIES.map((q) => (
                  <button
                    key={q}
                    onClick={() => handleSuggestion(q)}
                    className="text-xs px-3 py-1.5 rounded border border-matrix-green/15
                               text-matrix-green/50 hover:text-matrix-green hover:border-matrix-green/40
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
              <p className="text-neon-pink/70 text-sm">{error}</p>
              <p className="text-matrix-green/50 text-xs mt-2">
                The explore pipeline runs automatically on startup. Try again in a few minutes.
              </p>
            </div>
          )}

          {/* Loading */}
          {loading && (
            <div className="text-center py-12" role="status" aria-live="polite">
              <div className="inline-block border border-matrix-green/20 rounded px-6 py-3">
                <span className="text-matrix-green/60 text-sm font-terminal animate-pulse">
                  Searching government records...
                </span>
              </div>
            </div>
          )}

          {/* Results */}
          {!loading && searched && results.length > 0 && (
            <div aria-live="polite">
              <p className="text-matrix-green/50 text-xs mb-4">
                {results.length} result{results.length !== 1 ? "s" : ""} for &ldquo;{query}&rdquo;
                {commentableOnly && (
                  <span className="text-emerald-400/70 ml-2">— open for comment only</span>
                )}
                <span className="text-matrix-green/30 ml-2">
                  — sorted by {sortOrder === "date" ? "newest first" : "relevance"}
                </span>
              </p>
              <div className="space-y-3">
                {results.map((r) => (
                  <ResultCard
                    key={`${r.id}-${r.distance}`}
                    result={r}
                    query={query}
                  />
                ))}
              </div>
            </div>
          )}

          {/* No results */}
          {!loading && searched && !error && results.length === 0 && (
            <div className="text-center py-12">
              <p className="text-matrix-green/50 text-sm mb-2">
                No results found for &ldquo;{query}&rdquo;
              </p>
              <p className="text-matrix-green/50 text-xs">
                Try a broader search term or adjust your filters.
              </p>
            </div>
          )}

          {/* Source attribution */}
          <div className="mt-12 text-center">
            <p className="text-matrix-green/50 text-xs max-w-lg mx-auto">
              Data sourced from the Congressional Record (GovInfo API), the
              Federal Register (federalregister.gov), and Supreme Court opinions
              (supremecourt.gov via Oyez). Comment links go directly to regulations.gov.
            </p>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
