"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import { safeHref, localDateStr, formatUtcDate } from "@/lib/formatting";
import { chamberColor, chamberBorder, chamberLabel } from "@/lib/chamber";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { useAsyncData } from "@/hooks/useAsyncData";
import {
  fetchExploreDocument,
  streamExploreDocumentSummary,
  parseExploreSummaryText,
  fetchDocumentComments,
  submitDocumentComment,
  type ExploreDocumentDetail,
  type ExploreDocumentSummary,
  type PublicComment,
} from "@/lib/api";

const formatDate = (dateStr: string): string =>
  formatUtcDate(
    dateStr,
    { weekday: "long", year: "numeric", month: "long", day: "numeric" },
    "en-US"
  );

function resolveSourceUrl(doc: ExploreDocumentDetail): string {
  if (doc.url) return doc.url;
  if (!doc.date) return "";
  const datePart = doc.date.replace(/-/g, "/");
  if (doc.docType === "Senate Floor Speech") {
    return `https://www.congress.gov/congressional-record/${datePart}/senate-section`;
  }
  if (doc.docType === "House Floor Speech") {
    return `https://www.congress.gov/congressional-record/${datePart}/house-section`;
  }
  if (doc.docType === "Supreme Court Opinion") {
    return "https://www.supremecourt.gov/opinions/slipopinion/25";
  }
  return "";
}

function scorecardHref(doc: ExploreDocumentDetail): string | null {
  if (!doc.politicianId) return null;
  return `/politicians/${doc.politicianId}`;
}

function isCommentOpen(doc: ExploreDocumentDetail): boolean {
  if (!doc.commentUrl || !doc.commentsCloseOn) return false;
  return doc.commentsCloseOn >= localDateStr();
}

function daysUntilClose(closeDate: string): number {
  const close = new Date(closeDate + "T23:59:59");
  const now = new Date();
  return Math.max(0, Math.ceil((close.getTime() - now.getTime()) / 86_400_000));
}

function formatCommentDate(dateStr: string): string {
  if (!dateStr) return "";
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return dateStr;
  }
}

function buildCommentTemplate(title: string): string {
  return `I am writing in response to the proposed rule: "${title}".

I am a member of the public affected by this regulation. [Describe how this rule affects you or your community.]

I urge the agency to consider the following: [State your specific concern, suggestion, or support.]

Thank you for the opportunity to submit a public comment.`.trim();
}

function HelpMeCommentPanel({ doc, remaining }: { doc: ExploreDocumentDetail; remaining: number }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const plainSummary =
    doc.summary || (doc.body ? doc.body.slice(0, 400) + (doc.body.length > 400 ? "…" : "") : "");

  function handleOpen() {
    if (!open) setDraft(buildCommentTemplate(doc.title));
    setOpen((v) => !v);
  }

  useEffect(() => {
    if (open) textareaRef.current?.focus();
  }, [open]);

  return (
    <div className="mt-3">
      <button
        onClick={handleOpen}
        aria-expanded={open}
        className="text-xs font-mono px-4 py-2 border border-phos/40
                   text-phos-mid hover:text-phos hover:bg-phos/10
                   transition-colors"
      >
        {open ? "CLOSE" : "HELP ME WRITE A COMMENT"}
      </button>

      {open && (
        <div className="mt-4 p-4 border border-phos/20 bg-phos/5 space-y-4">
          {plainSummary && (
            <div>
              <p className="text-xs font-mono text-ink-lo mb-1 tracking-wider">
                WHAT THIS DOCUMENT DOES
              </p>
              <p className="text-base text-ink leading-relaxed">{plainSummary}</p>
            </div>
          )}

          <div>
            <label
              htmlFor="comment-draft"
              className="text-xs font-mono text-ink-lo block mb-1 tracking-wider"
            >
              YOUR COMMENT — EDIT BEFORE SUBMITTING
            </label>
            <textarea
              id="comment-draft"
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={9}
              className="w-full bg-surface-base border border-white/[0.07] px-3 py-2
                         text-sm text-ink-hi leading-relaxed
                         focus:outline-none focus:border-phos/50 transition-colors
                         resize-y min-h-[180px]"
            />
            <p className="text-xs text-ink-min mt-1">
              Replace the bracketed text with your own words.
            </p>
          </div>

          <div className="flex items-center justify-between gap-3 flex-wrap">
            <p className="text-xs text-ink-lo">
              {remaining === 0
                ? "Closes today"
                : `${remaining} day${remaining !== 1 ? "s" : ""} left`}{" "}
              · Opens on regulations.gov
            </p>
            <a
              href={safeHref(doc.commentUrl) || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-mono px-5 py-2 bg-phos/20 text-phos border border-phos/50
                         hover:bg-phos/30 hover:border-phos/70
                         transition-colors"
            >
              OPEN COMMENT FORM →
            </a>
          </div>
          <p className="text-xs text-ink-min leading-relaxed">
            Copy your comment above, then paste it into the form on regulations.gov. Your comment
            becomes part of the official public record.
          </p>
        </div>
      )}
    </div>
  );
}

function CommentsSection({
  docId,
  commentUrl,
  commentOpen,
  remaining,
}: {
  docId: number;
  commentUrl: string;
  commentOpen: boolean;
  remaining: number;
}) {
  const [comments, setComments] = useState<PublicComment[]>([]);
  const [totalComments, setTotalComments] = useState(0);
  const [commentsPage, setCommentsPage] = useState(1);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentsError, setCommentsError] = useState("");
  const [commentsLoaded, setCommentsLoaded] = useState(false);

  const [showForm, setShowForm] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [submitterName, setSubmitterName] = useState("");
  const [organization, setOrganization] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<{ success: boolean; message: string } | null>(
    null
  );

  const loadComments = useCallback(
    async (page: number) => {
      setCommentsLoading(true);
      setCommentsError("");
      try {
        const data = await fetchDocumentComments(docId, page);
        if (data.error) {
          setCommentsError(data.error);
        } else {
          setComments(data.comments || []);
          setTotalComments(data.totalElements || 0);
          setCommentsPage(page);
        }
        setCommentsLoaded(true);
      } catch (e) {
        setCommentsError(e instanceof Error ? e.message : "Failed to load comments");
        setCommentsLoaded(true);
      } finally {
        setCommentsLoading(false);
      }
    },
    [docId]
  );

  const handleSubmit = async () => {
    if (submitting || commentText.trim().length < 10) return;
    setSubmitting(true);
    setSubmitResult(null);
    try {
      const result = await submitDocumentComment(docId, commentText, submitterName, organization);
      setSubmitResult(result);
      if (result.success) {
        setCommentText("");
        setSubmitterName("");
        setOrganization("");
        setShowForm(false);
        if (commentsLoaded) loadComments(1);
      }
    } catch {
      setSubmitResult({ success: false, message: "Submission failed. Please try again." });
    } finally {
      setSubmitting(false);
    }
  };

  const totalPages = Math.ceil(totalComments / 25);

  return (
    <div className="mt-6">
      <div className="panel">
        <TerminalTitlebar title="Public comments" />
        <div className="p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xs font-mono text-ink-lo tracking-wider">
              PUBLIC COMMENTS
              {commentsLoaded && totalComments > 0 && (
                <span className="ml-2 text-ink-min">({totalComments})</span>
              )}
            </h3>
            <div className="flex items-center gap-3">
              {commentOpen && (
                <button
                  onClick={() => {
                    setShowForm(!showForm);
                    setSubmitResult(null);
                  }}
                  className="text-xs font-mono px-3 py-1.5 bg-phos/20 text-phos border border-phos/40
                             hover:bg-phos/30 transition-colors"
                >
                  {showForm ? "CANCEL" : "WRITE COMMENT"}
                </button>
              )}
              {!commentsLoaded && (
                <button
                  onClick={() => loadComments(1)}
                  disabled={commentsLoading}
                  className="text-xs font-mono px-3 py-1.5 bg-signal-cyan/10 text-signal-cyan border border-white/15
                             hover:bg-signal-cyan/10 hover:text-phos transition-colors
                             disabled:opacity-50"
                >
                  {commentsLoading ? "LOADING..." : "LOAD COMMENTS"}
                </button>
              )}
            </div>
          </div>

          {/* Submit Result Banner */}
          {submitResult && (
            <div
              role="alert"
              className={`mb-4 p-3  border text-sm ${
                submitResult.success
                  ? "bg-phos/10 border-phos/30 text-phos"
                  : "bg-signal-red/10 border-signal-red/40 text-signal-red"
              }`}
            >
              {submitResult.message}
            </div>
          )}

          {/* Comment Form */}
          {showForm && commentOpen && (
            <div className="mb-6 p-4 border border-phos/20 bg-phos/5">
              <p className="text-xs text-ink-lo mb-3 leading-relaxed">
                Your comment will be submitted to regulations.gov and become part of the official
                public record. Agency officials review these comments when making final decisions.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
                <div>
                  <label
                    htmlFor="comment-name"
                    className="text-xs font-mono text-ink-min block mb-1"
                  >
                    YOUR NAME
                  </label>
                  <input
                    id="comment-name"
                    type="text"
                    value={submitterName}
                    onChange={(e) => setSubmitterName(e.target.value)}
                    placeholder="Anonymous"
                    maxLength={100}
                    className="w-full bg-surface-base border border-white/[0.07] px-3 py-2
                               text-sm text-ink-hi placeholder:text-ink-min
                               focus:outline-none focus:border-phos/50 transition-colors"
                  />
                </div>
                <div>
                  <label
                    htmlFor="comment-org"
                    className="text-xs font-mono text-ink-min block mb-1"
                  >
                    ORGANIZATION <span className="text-ink-min">(OPTIONAL)</span>
                  </label>
                  <input
                    id="comment-org"
                    type="text"
                    value={organization}
                    onChange={(e) => setOrganization(e.target.value)}
                    placeholder=""
                    maxLength={200}
                    className="w-full bg-surface-base border border-white/[0.07] px-3 py-2
                               text-sm text-ink-hi placeholder:text-ink-min
                               focus:outline-none focus:border-phos/50 transition-colors"
                  />
                </div>
              </div>

              <div className="mb-3">
                <label htmlFor="comment-text" className="text-xs font-mono text-ink-min block mb-1">
                  YOUR COMMENT
                </label>
                <textarea
                  id="comment-text"
                  value={commentText}
                  onChange={(e) => setCommentText(e.target.value)}
                  placeholder="Share your perspective on this proposed rule or regulation..."
                  maxLength={5000}
                  rows={6}
                  className="w-full bg-surface-base border border-white/[0.07] px-3 py-2
                             text-sm text-ink-hi placeholder:text-ink-min
                             focus:outline-none focus:border-phos/50 transition-colors
                             resize-y min-h-[120px]"
                />
                <div className="flex justify-between mt-1">
                  <span className="text-xs text-ink-min">Minimum 10 characters</span>
                  <span
                    className={`text-xs ${
                      commentText.length > 4800 ? "text-signal-red" : "text-ink-min"
                    }`}
                  >
                    {commentText.length}/5000
                  </span>
                </div>
              </div>

              <div className="flex items-center justify-between">
                <p className="text-xs text-ink-min">
                  {remaining} day{remaining !== 1 ? "s" : ""} remaining to comment
                </p>
                <button
                  onClick={handleSubmit}
                  disabled={submitting || commentText.trim().length < 10}
                  className="text-xs font-mono px-6 py-2 bg-phos/20 text-phos border border-phos/50
                             hover:bg-phos/30 hover:border-phos/70
                             transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {submitting ? "SUBMITTING..." : "SUBMIT TO OFFICIAL RECORD"}
                </button>
              </div>

              <p className="text-xs text-ink-min mt-3 leading-relaxed">
                By submitting, you acknowledge this comment will be publicly visible on
                regulations.gov. Do not include personal information you do not wish to be public.
              </p>
            </div>
          )}

          {/* Comments List */}
          {commentsLoading && (
            <div className="text-center py-8">
              <span className="text-signal-cyan text-sm font-mono animate-pulse">
                Loading public comments...
              </span>
            </div>
          )}

          {commentsError && (
            <div className="text-center py-6">
              <p className="text-ink-min text-base">{commentsError}</p>
              {commentsError === "API key not configured" && (
                <a
                  href={safeHref(commentUrl) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block mt-3 text-xs font-mono text-signal-cyan hover:text-phos transition-colors"
                >
                  VIEW COMMENTS ON REGULATIONS.GOV →
                </a>
              )}
            </div>
          )}

          {commentsLoaded && !commentsLoading && !commentsError && comments.length === 0 && (
            <div className="text-center py-6">
              <p className="text-ink-min text-base">
                {totalComments === 0
                  ? "No public comments have been submitted yet."
                  : "No comments on this page."}
              </p>
              {commentOpen && !showForm && (
                <button
                  onClick={() => setShowForm(true)}
                  className="mt-3 text-xs font-mono text-phos-mid hover:text-phos transition-colors"
                >
                  BE THE FIRST TO COMMENT →
                </button>
              )}
            </div>
          )}

          {commentsLoaded && !commentsLoading && comments.length > 0 && (
            <div className="space-y-4">
              {comments.map((c) => (
                <div key={c.id} className="border border-white/[0.07] p-4 bg-phos/[0.02]">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-ink font-medium">
                        {c.submitterName || "Anonymous"}
                      </span>
                      {c.organization && (
                        <span className="text-xs text-ink-min">({c.organization})</span>
                      )}
                    </div>
                    <span className="text-xs text-ink-min">{formatCommentDate(c.postedDate)}</span>
                  </div>
                  {c.title && c.title !== c.body?.slice(0, 50) && (
                    <p className="text-xs text-ink-lo font-medium mb-1">{c.title}</p>
                  )}
                  <p className="text-base text-ink-lo leading-relaxed whitespace-pre-wrap">
                    {c.body || "(No comment text available)"}
                  </p>
                </div>
              ))}

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-4 pt-4">
                  <button
                    onClick={() => loadComments(commentsPage - 1)}
                    disabled={commentsPage <= 1 || commentsLoading}
                    className="text-xs font-mono text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed transition-colors"
                  >
                    ← PREV
                  </button>
                  <span className="text-xs text-ink-min">
                    Page {commentsPage} of {totalPages}
                  </span>
                  <button
                    onClick={() => loadComments(commentsPage + 1)}
                    disabled={commentsPage >= totalPages || commentsLoading}
                    className="text-xs font-mono text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed transition-colors"
                  >
                    NEXT →
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Link to regulations.gov */}
          {commentsLoaded && (
            <div className="mt-4 pt-3 border-t border-white/[0.07] text-center">
              <a
                href={safeHref(commentUrl) || "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs font-mono text-ink-min hover:text-phos transition-colors"
              >
                VIEW ALL COMMENTS ON REGULATIONS.GOV →
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ExploreDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const docId = Number(params.id);
  const query = searchParams.get("q") || "";

  // liveText accumulates as the stream arrives; summary is set once the
  // stream's terminal event resolves (also the ONLY thing that fires on a
  // cache hit — no deltas at all — so it can't be dropped in favor of just
  // deriving from liveText). While streaming, the summary panel renders
  // parseExploreSummaryText(liveText) instead for progressive display.
  const [liveText, setLiveText] = useState("");
  const [summary, setSummary] = useState<ExploreDocumentSummary | null>(null);

  const docRequest = useAsyncData(
    `explore-doc:${docId || ""}`,
    docId ? () => fetchExploreDocument(docId) : null
  );
  const doc = docRequest.data;
  const loading = docRequest.loading;
  const docError = docRequest.error;

  // The summary is streamed token-by-token, so it is a real side effect
  // rather than a keyed fetch. A ref is the re-entry guard so nothing has to
  // be set synchronously in the effect body.
  const streamStarted = useRef<number | null>(null);

  useEffect(() => {
    if (!docId || streamStarted.current === docId || summary) return;
    streamStarted.current = docId;
    streamExploreDocumentSummary(docId, setLiveText)
      .then(setSummary)
      .catch(() => {
        setSummary({
          summary: "Analysis unavailable. Try again later.",
          keyPoints: [],
          impact: "",
        });
      });
  }, [docId, summary]);

  // Both the success and the failure path of the stream set `summary`, so
  // "still streaming" is exactly "asked for one and none has landed" — no
  // separate flag to keep in sync.
  const summaryStreaming = Boolean(docId) && summary === null;

  const displayedSummary = summary ?? (liveText ? parseExploreSummaryText(liveText) : null);

  if (loading) {
    return (
      <>
        <Navbar />
        <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
          <div className="max-w-3xl mx-auto text-center py-20">
            <span className="text-ink-hi font-mono animate-pulse">Loading document...</span>
          </div>
        </main>
        <Footer />
      </>
    );
  }

  if (docError || !doc) {
    return (
      <>
        <Navbar />
        <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
          <div className="max-w-3xl mx-auto text-center py-20">
            <p className="text-signal-magenta text-base mb-4">{docError || "Document not found"}</p>
            <Link
              href="/explore"
              className="text-xs font-mono text-signal-cyan hover:text-phos transition-colors"
            >
              ← BACK TO EXPLORE
            </Link>
          </div>
        </main>
        <Footer />
      </>
    );
  }

  const sourceUrl = resolveSourceUrl(doc);
  const scorecardLink = scorecardHref(doc);
  const sourceLabel =
    doc.docType === "Senate Floor Speech" || doc.docType === "House Floor Speech"
      ? "Congressional Record"
      : doc.chamber === "Executive"
        ? "Federal Register"
        : doc.chamber === "Judicial"
          ? "Supreme Court of the United States"
          : doc.chamber === "Regulatory"
            ? "Federal Register"
            : doc.source;
  const commentOpen = isCommentOpen(doc);
  const remaining = commentOpen ? daysUntilClose(doc.commentsCloseOn) : 0;

  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          {/* Back link */}
          <Link
            href={query ? `/explore?q=${encodeURIComponent(query)}` : "/explore"}
            className="inline-block text-xs font-mono text-ink-lo hover:text-phos transition-colors mb-6"
          >
            ← BACK TO RESULTS
          </Link>

          {/* Public Comment CTA — prominent, above everything */}
          {commentOpen && (
            <div className="border border-phos/40 p-5 mb-6 bg-phos/5">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-mono px-2 py-0.5 bg-phos/20 text-phos border border-phos/30">
                      OPEN FOR PUBLIC COMMENT
                    </span>
                    <span className="text-ink-lo text-xs">
                      {remaining === 0
                        ? "Closes today!"
                        : `${remaining} day${remaining !== 1 ? "s" : ""} remaining`}
                    </span>
                  </div>
                  <p className="text-base text-ink leading-relaxed">
                    The public can submit comments on this document. Your input is part of the
                    official record and may influence the final outcome.
                  </p>
                  <p className="text-xs text-ink-min mt-1">
                    Comments close {formatDate(doc.commentsCloseOn)}
                  </p>
                </div>
                <a
                  href={safeHref(doc.commentUrl) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-mono px-6 py-3 bg-phos/20 text-phos border border-phos/50
                             hover:bg-phos/30 hover:border-phos/70
                             transition-colors shrink-0"
                >
                  SUBMIT YOUR COMMENT →
                </a>
              </div>
              <HelpMeCommentPanel doc={doc} remaining={remaining} />
            </div>
          )}

          {/* Document header */}
          <div className={`border p-6 mb-6 ${chamberBorder(doc.chamber)} bg-surface-base`}>
            <div className="flex items-center gap-2 flex-wrap mb-3">
              <span className={`text-xs font-mono tracking-wider ${chamberColor(doc.chamber)}`}>
                {chamberLabel(doc.chamber)}
              </span>
              <span className="text-ink-min text-xs">|</span>
              <span className="text-ink-lo text-xs">{doc.docType}</span>
              {doc.date && (
                <>
                  <span className="text-ink-min text-xs">|</span>
                  <span className="text-ink-lo text-xs">{formatDate(doc.date)}</span>
                </>
              )}
            </div>

            <h1 className="text-lg sm:text-xl text-ink-hi font-medium leading-snug mb-4">
              {doc.title}
            </h1>

            <div className="flex items-center gap-4 flex-wrap text-xs">
              {doc.agencyName && <span className="text-signal-orange">{doc.agencyName}</span>}
              {doc.politicianName && !doc.agencyName && (
                <span className="text-ink">{doc.politicianName}</span>
              )}
              {scorecardLink && (
                <Link
                  href={scorecardLink}
                  className="font-mono text-xs text-signal-cyan hover:text-phos transition-colors"
                >
                  [VIEW SCORECARD]
                </Link>
              )}
              {sourceUrl && (
                <a
                  href={safeHref(sourceUrl) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono text-xs text-ink-min hover:text-phos transition-colors"
                >
                  [{sourceLabel.toUpperCase()}]
                </a>
              )}
            </div>
          </div>

          {/* AI Analysis section */}
          <div className="panel mb-6">
            <TerminalTitlebar title="Analysis" />
            <div className="p-5">
              {summaryStreaming && !displayedSummary && (
                <div className="text-center py-6">
                  <span className="text-signal-cyan text-sm font-mono animate-pulse">
                    Analyzing document...
                  </span>
                  <p className="text-ink-min text-xs mt-2">This may take a moment</p>
                </div>
              )}

              {displayedSummary && (
                <div className="space-y-4" aria-live="polite">
                  {displayedSummary.summary && (
                    <div>
                      <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">
                        AI SUMMARY
                      </h3>
                      <p className="text-base text-ink-hi leading-relaxed">
                        {displayedSummary.summary}
                        {summaryStreaming && !summary && (
                          <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-signal-cyan animate-pulse align-middle" />
                        )}
                      </p>
                    </div>
                  )}

                  {displayedSummary.keyPoints.length > 0 && (
                    <div>
                      <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">
                        KEY POINTS
                      </h3>
                      <ul className="space-y-1.5">
                        {displayedSummary.keyPoints.map((point, i) => (
                          <li key={i} className="flex gap-2 text-sm text-ink">
                            <span className="text-ink-lo shrink-0">▸</span>
                            <span className="leading-relaxed">{point}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {displayedSummary.impact && (
                    <div className="border-t border-white/[0.07] pt-3">
                      <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">IMPACT</h3>
                      <p className="text-base text-ink leading-relaxed">
                        {displayedSummary.impact}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Document body */}
          <div className="panel">
            <TerminalTitlebar title="Document" />
            <div className="p-5">
              {doc.summary && doc.summary !== doc.body?.slice(0, 300) && (
                <div className="mb-4 pb-4 border-b border-white/[0.07]">
                  <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">SUMMARY</h3>
                  <p className="text-base text-ink leading-relaxed">{doc.summary}</p>
                </div>
              )}

              {doc.body && (
                <div>
                  <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">FULL TEXT</h3>
                  <div className="text-sm text-ink leading-relaxed whitespace-pre-wrap max-h-[600px] overflow-y-auto pr-2">
                    {doc.body}
                  </div>
                </div>
              )}

              {!doc.body && !doc.summary && (
                <p className="text-ink-min text-base">No document content available.</p>
              )}
            </div>
          </div>

          {/* Public Comments Section */}
          {doc.commentUrl && (
            <CommentsSection
              docId={doc.id}
              commentUrl={doc.commentUrl}
              commentOpen={commentOpen}
              remaining={remaining}
            />
          )}

          {/* Source attribution */}
          <div className="mt-8 text-center">
            <p className="text-ink-lo text-xs">
              Source: {sourceLabel}
              {sourceUrl && (
                <>
                  {" "}
                  —{" "}
                  <a
                    href={safeHref(sourceUrl) || "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-ink-min hover:text-phos underline transition-colors"
                  >
                    View original
                  </a>
                </>
              )}
            </p>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
