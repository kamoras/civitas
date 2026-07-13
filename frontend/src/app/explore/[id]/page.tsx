"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import { safeHref, localDateStr } from "@/lib/formatting";
import { chamberColor, chamberBorder, chamberLabel } from "@/lib/chamber";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import {
  fetchExploreDocument,
  fetchExploreDocumentSummary,
  fetchDocumentComments,
  submitDocumentComment,
  type ExploreDocumentDetail,
  type ExploreDocumentSummary,
  type PublicComment,
} from "@/lib/api";

function formatDate(dateStr: string): string {
  if (!dateStr) return "";
  try {
    const d = new Date(dateStr + "T00:00:00");
    return d.toLocaleDateString("en-US", {
      weekday: "long",
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

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

function HelpMeCommentPanel({
  doc,
  remaining,
}: {
  doc: ExploreDocumentDetail;
  remaining: number;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const plainSummary = doc.summary || (doc.body ? doc.body.slice(0, 400) + (doc.body.length > 400 ? "…" : "") : "");

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
        className="text-xs font-pixel px-4 py-2 rounded border border-emerald-500/40
                   text-emerald-400/80 hover:text-emerald-400 hover:bg-emerald-500/10
                   transition-colors"
      >
        {open ? "CLOSE" : "HELP ME WRITE A COMMENT"}
      </button>

      {open && (
        <div className="mt-4 p-4 border border-emerald-500/20 rounded bg-emerald-500/5 space-y-4">
          {plainSummary && (
            <div>
              <p className="text-[10px] font-pixel text-emerald-400/50 mb-1 tracking-wider">WHAT THIS DOCUMENT DOES</p>
              <p className="text-sm text-matrix-green/70 leading-relaxed">{plainSummary}</p>
            </div>
          )}

          <div>
            <label htmlFor="comment-draft" className="text-[10px] font-pixel text-matrix-green/50 block mb-1 tracking-wider">
              YOUR COMMENT — EDIT BEFORE SUBMITTING
            </label>
            <textarea
              id="comment-draft"
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={9}
              className="w-full bg-crt-black/80 border border-matrix-green/20 rounded px-3 py-2
                         text-sm text-matrix-green leading-relaxed
                         focus:outline-none focus:border-emerald-500/50 transition-colors
                         resize-y min-h-[180px]"
            />
            <p className="text-[10px] text-matrix-green/30 mt-1">
              Replace the bracketed text with your own words.
            </p>
          </div>

          <div className="flex items-center justify-between gap-3 flex-wrap">
            <p className="text-[10px] text-emerald-400/50">
              {remaining === 0 ? "Closes today" : `${remaining} day${remaining !== 1 ? "s" : ""} left`} · Opens on regulations.gov
            </p>
            <a
              href={safeHref(doc.commentUrl) || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-pixel px-5 py-2 rounded
                         bg-emerald-500/20 text-emerald-400 border border-emerald-500/50
                         hover:bg-emerald-500/30 hover:border-emerald-500/70
                         transition-colors"
            >
              OPEN COMMENT FORM →
            </a>
          </div>
          <p className="text-[10px] text-matrix-green/25 leading-relaxed">
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
  const [submitResult, setSubmitResult] = useState<{ success: boolean; message: string } | null>(null);

  const loadComments = useCallback(async (page: number) => {
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
  }, [docId]);

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
      <div className="terminal-window">
        <TerminalTitlebar title="public_comments.sh" />
        <div className="p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[10px] font-pixel text-matrix-green/50 tracking-wider">
              PUBLIC COMMENTS
              {commentsLoaded && totalComments > 0 && (
                <span className="ml-2 text-matrix-green/30">({totalComments})</span>
              )}
            </h3>
            <div className="flex items-center gap-3">
              {commentOpen && (
                <button
                  onClick={() => { setShowForm(!showForm); setSubmitResult(null); }}
                  className="text-[10px] font-pixel px-3 py-1.5 rounded
                             bg-emerald-500/20 text-emerald-400 border border-emerald-500/40
                             hover:bg-emerald-500/30 transition-colors"
                >
                  {showForm ? "CANCEL" : "WRITE COMMENT"}
                </button>
              )}
              {!commentsLoaded && (
                <button
                  onClick={() => loadComments(1)}
                  disabled={commentsLoading}
                  className="text-[10px] font-pixel px-3 py-1.5 rounded
                             bg-neon-cyan/10 text-neon-cyan/70 border border-neon-cyan/30
                             hover:bg-neon-cyan/20 hover:text-neon-cyan transition-colors
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
              className={`mb-4 p-3 rounded border text-sm ${
                submitResult.success
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                  : "bg-red-500/10 border-red-500/30 text-red-400"
              }`}
            >
              {submitResult.message}
            </div>
          )}

          {/* Comment Form */}
          {showForm && commentOpen && (
            <div className="mb-6 p-4 border border-emerald-500/20 rounded bg-emerald-500/5">
              <p className="text-xs text-matrix-green/60 mb-3 leading-relaxed">
                Your comment will be submitted to regulations.gov and become part of the
                official public record. Agency officials review these comments when
                making final decisions.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
                <div>
                  <label htmlFor="comment-name" className="text-[10px] font-pixel text-matrix-green/40 block mb-1">
                    YOUR NAME
                  </label>
                  <input
                    id="comment-name"
                    type="text"
                    value={submitterName}
                    onChange={(e) => setSubmitterName(e.target.value)}
                    placeholder="Anonymous"
                    maxLength={100}
                    className="w-full bg-crt-black/80 border border-matrix-green/20 rounded px-3 py-2
                               text-sm text-matrix-green placeholder:text-matrix-green/20
                               focus:outline-none focus:border-emerald-500/50 transition-colors"
                  />
                </div>
                <div>
                  <label htmlFor="comment-org" className="text-[10px] font-pixel text-matrix-green/40 block mb-1">
                    ORGANIZATION <span className="text-matrix-green/30">(OPTIONAL)</span>
                  </label>
                  <input
                    id="comment-org"
                    type="text"
                    value={organization}
                    onChange={(e) => setOrganization(e.target.value)}
                    placeholder=""
                    maxLength={200}
                    className="w-full bg-crt-black/80 border border-matrix-green/20 rounded px-3 py-2
                               text-sm text-matrix-green placeholder:text-matrix-green/20
                               focus:outline-none focus:border-emerald-500/50 transition-colors"
                  />
                </div>
              </div>

              <div className="mb-3">
                <label htmlFor="comment-text" className="text-[10px] font-pixel text-matrix-green/40 block mb-1">
                  YOUR COMMENT
                </label>
                <textarea
                  id="comment-text"
                  value={commentText}
                  onChange={(e) => setCommentText(e.target.value)}
                  placeholder="Share your perspective on this proposed rule or regulation..."
                  maxLength={5000}
                  rows={6}
                  className="w-full bg-crt-black/80 border border-matrix-green/20 rounded px-3 py-2
                             text-sm text-matrix-green placeholder:text-matrix-green/20
                             focus:outline-none focus:border-emerald-500/50 transition-colors
                             resize-y min-h-[120px]"
                />
                <div className="flex justify-between mt-1">
                  <span className="text-[10px] text-matrix-green/30">
                    Minimum 10 characters
                  </span>
                  <span className={`text-[10px] ${
                    commentText.length > 4800 ? "text-red-400" : "text-matrix-green/30"
                  }`}>
                    {commentText.length}/5000
                  </span>
                </div>
              </div>

              <div className="flex items-center justify-between">
                <p className="text-[10px] text-matrix-green/30">
                  {remaining} day{remaining !== 1 ? "s" : ""} remaining to comment
                </p>
                <button
                  onClick={handleSubmit}
                  disabled={submitting || commentText.trim().length < 10}
                  className="text-xs font-pixel px-6 py-2 rounded
                             bg-emerald-500/20 text-emerald-400 border border-emerald-500/50
                             hover:bg-emerald-500/30 hover:border-emerald-500/70
                             transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {submitting ? "SUBMITTING..." : "SUBMIT TO OFFICIAL RECORD"}
                </button>
              </div>

              <p className="text-[10px] text-matrix-green/25 mt-3 leading-relaxed">
                By submitting, you acknowledge this comment will be publicly visible on
                regulations.gov. Do not include personal information you do not wish to be public.
              </p>
            </div>
          )}

          {/* Comments List */}
          {commentsLoading && (
            <div className="text-center py-8">
              <span className="text-neon-cyan text-sm font-terminal animate-pulse">
                Loading public comments...
              </span>
            </div>
          )}

          {commentsError && (
            <div className="text-center py-6">
              <p className="text-matrix-green/40 text-sm">{commentsError}</p>
              {commentsError === "API key not configured" && (
                <a
                  href={safeHref(commentUrl) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block mt-3 text-[10px] font-pixel text-neon-cyan/70 hover:text-neon-cyan transition-colors"
                >
                  VIEW COMMENTS ON REGULATIONS.GOV →
                </a>
              )}
            </div>
          )}

          {commentsLoaded && !commentsLoading && !commentsError && comments.length === 0 && (
            <div className="text-center py-6">
              <p className="text-matrix-green/40 text-sm">
                {totalComments === 0
                  ? "No public comments have been submitted yet."
                  : "No comments on this page."}
              </p>
              {commentOpen && !showForm && (
                <button
                  onClick={() => setShowForm(true)}
                  className="mt-3 text-[10px] font-pixel text-emerald-400/70 hover:text-emerald-400 transition-colors"
                >
                  BE THE FIRST TO COMMENT →
                </button>
              )}
            </div>
          )}

          {commentsLoaded && !commentsLoading && comments.length > 0 && (
            <div className="space-y-4">
              {comments.map((c) => (
                <div
                  key={c.id}
                  className="border border-matrix-green/10 rounded p-4 bg-matrix-green/[0.02]"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-matrix-green/70 font-medium">
                        {c.submitterName || "Anonymous"}
                      </span>
                      {c.organization && (
                        <span className="text-[10px] text-matrix-green/30">
                          ({c.organization})
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] text-matrix-green/30">
                      {formatCommentDate(c.postedDate)}
                    </span>
                  </div>
                  {c.title && c.title !== c.body?.slice(0, 50) && (
                    <p className="text-xs text-matrix-green/60 font-medium mb-1">{c.title}</p>
                  )}
                  <p className="text-sm text-matrix-green/60 leading-relaxed whitespace-pre-wrap">
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
                    className="text-[10px] font-pixel text-neon-cyan/60 hover:text-neon-cyan
                               disabled:text-matrix-green/20 disabled:cursor-not-allowed transition-colors"
                  >
                    ← PREV
                  </button>
                  <span className="text-[10px] text-matrix-green/40">
                    Page {commentsPage} of {totalPages}
                  </span>
                  <button
                    onClick={() => loadComments(commentsPage + 1)}
                    disabled={commentsPage >= totalPages || commentsLoading}
                    className="text-[10px] font-pixel text-neon-cyan/60 hover:text-neon-cyan
                               disabled:text-matrix-green/20 disabled:cursor-not-allowed transition-colors"
                  >
                    NEXT →
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Link to regulations.gov */}
          {commentsLoaded && (
            <div className="mt-4 pt-3 border-t border-matrix-green/10 text-center">
              <a
                href={safeHref(commentUrl) || "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10px] font-pixel text-matrix-green/30 hover:text-matrix-green/60 transition-colors"
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

  const [doc, setDoc] = useState<ExploreDocumentDetail | null>(null);
  const [summary, setSummary] = useState<ExploreDocumentSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!docId) return;
    setLoading(true);
    fetchExploreDocument(docId)
      .then(setDoc)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load document"))
      .finally(() => setLoading(false));
  }, [docId]);

  useEffect(() => {
    if (!docId || summaryLoading || summary) return;
    setSummaryLoading(true);
    fetchExploreDocumentSummary(docId)
      .then(setSummary)
      .catch(() => {
        setSummary({ summary: "Analysis unavailable. Try again later.", keyPoints: [], impact: "" });
      })
      .finally(() => setSummaryLoading(false));
  }, [docId, summaryLoading, summary]);

  if (loading) {
    return (
      <>
        <MatrixRain />
        <Navbar />
        <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
          <div className="max-w-3xl mx-auto text-center py-20">
            <span className="text-matrix-green font-terminal animate-pulse">
              Loading document...
            </span>
          </div>
        </main>
        <Footer />
      </>
    );
  }

  if (error || !doc) {
    return (
      <>
        <MatrixRain />
        <Navbar />
        <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
          <div className="max-w-3xl mx-auto text-center py-20">
            <p className="text-neon-pink text-sm mb-4">{error || "Document not found"}</p>
            <Link
              href="/explore"
              className="text-[10px] font-pixel text-neon-cyan/70 hover:text-neon-cyan transition-colors"
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
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          {/* Back link */}
          <Link
            href={query ? `/explore?q=${encodeURIComponent(query)}` : "/explore"}
            className="inline-block text-[10px] font-pixel text-matrix-green/50 hover:text-matrix-green
                       transition-colors mb-6"
          >
            ← BACK TO RESULTS
          </Link>

          {/* Public Comment CTA — prominent, above everything */}
          {commentOpen && (
            <div className="border border-emerald-500/40 rounded-lg p-5 mb-6 bg-emerald-500/5">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-pixel px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                      OPEN FOR PUBLIC COMMENT
                    </span>
                    <span className="text-emerald-400/60 text-xs">
                      {remaining === 0
                        ? "Closes today!"
                        : `${remaining} day${remaining !== 1 ? "s" : ""} remaining`}
                    </span>
                  </div>
                  <p className="text-sm text-matrix-green/70 leading-relaxed">
                    The public can submit comments on this document. Your input
                    is part of the official record and may influence the final outcome.
                  </p>
                  <p className="text-xs text-matrix-green/40 mt-1">
                    Comments close {formatDate(doc.commentsCloseOn)}
                  </p>
                </div>
                <a
                  href={safeHref(doc.commentUrl) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-pixel px-6 py-3 rounded
                             bg-emerald-500/20 text-emerald-400 border border-emerald-500/50
                             hover:bg-emerald-500/30 hover:border-emerald-500/70
                             transition-colors shrink-0"
                >
                  SUBMIT YOUR COMMENT →
                </a>
              </div>
              <HelpMeCommentPanel doc={doc} remaining={remaining} />
            </div>
          )}

          {/* Document header */}
          <div className={`border rounded-lg p-6 mb-6 ${chamberBorder(doc.chamber)} bg-crt-black/50`}>
            <div className="flex items-center gap-2 flex-wrap mb-3">
              <span className={`text-[10px] font-pixel tracking-wider ${chamberColor(doc.chamber)}`}>
                {chamberLabel(doc.chamber)}
              </span>
              <span className="text-matrix-green/30 text-xs">|</span>
              <span className="text-matrix-green/50 text-xs">{doc.docType}</span>
              {doc.date && (
                <>
                  <span className="text-matrix-green/30 text-xs">|</span>
                  <span className="text-matrix-green/50 text-xs">{formatDate(doc.date)}</span>
                </>
              )}
            </div>

            <h1 className="text-lg sm:text-xl text-matrix-green font-medium leading-snug mb-4">
              {doc.title}
            </h1>

            <div className="flex items-center gap-4 flex-wrap text-xs">
              {doc.agencyName && (
                <span className="text-orange-400/70">{doc.agencyName}</span>
              )}
              {doc.politicianName && !doc.agencyName && (
                <span className="text-matrix-green/70">{doc.politicianName}</span>
              )}
              {scorecardLink && (
                <Link
                  href={scorecardLink}
                  className="font-pixel text-[10px] text-neon-cyan/70 hover:text-neon-cyan transition-colors"
                >
                  [VIEW SCORECARD]
                </Link>
              )}
              {sourceUrl && (
                <a
                  href={safeHref(sourceUrl) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-pixel text-[10px] text-matrix-green/40 hover:text-matrix-green transition-colors"
                >
                  [{sourceLabel.toUpperCase()}]
                </a>
              )}
            </div>
          </div>

          {/* AI Analysis section */}
          <div className="terminal-window mb-6">
            <TerminalTitlebar title="ai_analysis.sh" />
            <div className="p-5">
              {summaryLoading && (
                <div className="text-center py-6">
                  <span className="text-neon-cyan text-sm font-terminal animate-pulse">
                    Analyzing document...
                  </span>
                  <p className="text-matrix-green/40 text-xs mt-2">
                    This may take a moment
                  </p>
                </div>
              )}

              {summary && !summaryLoading && (
                <div className="space-y-4" aria-live="polite">
                  {summary.summary && (
                    <div>
                      <h3 className="text-[10px] font-pixel text-neon-cyan/60 tracking-wider mb-2">
                        AI SUMMARY
                      </h3>
                      <p className="text-sm text-matrix-green/90 leading-relaxed">
                        {summary.summary}
                      </p>
                    </div>
                  )}

                  {summary.keyPoints.length > 0 && (
                    <div>
                      <h3 className="text-[10px] font-pixel text-neon-cyan/60 tracking-wider mb-2">
                        KEY POINTS
                      </h3>
                      <ul className="space-y-1.5">
                        {summary.keyPoints.map((point, i) => (
                          <li key={i} className="flex gap-2 text-sm text-matrix-green/80">
                            <span className="text-neon-cyan/50 shrink-0">▸</span>
                            <span className="leading-relaxed">{point}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {summary.impact && (
                    <div className="border-t border-matrix-green/15 pt-3">
                      <h3 className="text-[10px] font-pixel text-neon-cyan/60 tracking-wider mb-2">
                        IMPACT
                      </h3>
                      <p className="text-sm text-matrix-green/80 leading-relaxed">
                        {summary.impact}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Document body */}
          <div className="terminal-window">
            <TerminalTitlebar title="document_content" />
            <div className="p-5">
              {doc.summary && doc.summary !== doc.body?.slice(0, 300) && (
                <div className="mb-4 pb-4 border-b border-matrix-green/15">
                  <h3 className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mb-2">
                    SUMMARY
                  </h3>
                  <p className="text-sm text-matrix-green/80 leading-relaxed">
                    {doc.summary}
                  </p>
                </div>
              )}

              {doc.body && (
                <div>
                  <h3 className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mb-2">
                    FULL TEXT
                  </h3>
                  <div className="text-sm text-matrix-green/70 leading-relaxed whitespace-pre-wrap max-h-[600px] overflow-y-auto pr-2">
                    {doc.body}
                  </div>
                </div>
              )}

              {!doc.body && !doc.summary && (
                <p className="text-matrix-green/40 text-sm">
                  No document content available.
                </p>
              )}
            </div>
          </div>

          {/* Public Comments Section */}
          {doc.commentUrl && (
            <CommentsSection docId={doc.id} commentUrl={doc.commentUrl} commentOpen={commentOpen} remaining={remaining} />
          )}

          {/* Source attribution */}
          <div className="mt-8 text-center">
            <p className="text-matrix-green/50 text-xs">
              Source: {sourceLabel}
              {sourceUrl && (
                <>
                  {" "}—{" "}
                  <a
                    href={safeHref(sourceUrl) || "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-matrix-green/40 hover:text-matrix-green underline transition-colors"
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
