import Link from "next/link";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";
import PaperworkFold from "@/components/shared/PaperworkFold";
import DocumentFilters from "@/components/search/DocumentFilters";
import {
  splitHighlights,
  type ExploreNotHeld,
  type ExploreResponse,
  type ExploreResult,
  type ExploreStats,
} from "@/lib/api";
import { formatUtcDate, isCommentPeriodOpen } from "@/lib/formatting";
import { chamberLabel } from "@/lib/chamber";
import { exploreHref } from "@/lib/exploreHref";
import { documentsHref } from "@/lib/routes";
import { DOCUMENT_LIMIT, asksForDocuments, type DocumentQuery } from "@/lib/documentSearch";

// Shown before a search: the topical list, plus two exact-term examples,
// since the keyword channel makes document numbers and "quoted phrases"
// work and this is where a reader finds that out.
const SUGGESTED_QUERIES = [
  "tariffs and trade policy",
  "healthcare costs and prescription drugs",
  "immigration and border security",
  "climate change and clean energy",
  "student loan forgiveness",
  "Supreme Court constitutional rights",
  "Executive Order 14110",
  '"clean water act"',
];

const formatDate = (dateStr: string): string =>
  formatUtcDate(dateStr, { year: "numeric", month: "short", day: "numeric" }, "en-US");

/** A backend excerpt with its matched query terms marked. The markers are
 * control characters, not markup: the text is a verbatim slice of a
 * government document and is never parsed as HTML. */
function Snippet({ text }: { text: string }) {
  const segments = splitHighlights(text);
  if (segments.length === 0) return null;
  return (
    <p className="-mt-2 line-clamp-3 font-sans text-sm leading-relaxed text-ink-lo">
      {segments.map((segment, i) =>
        segment.match ? (
          // Ink on a light wash, underlined: the ledger green over a tinted card
          // measured 4.34:1 (axe, WCAG 1.4.3), and the underline marks the
          // match without colour alone (1.4.1).
          <mark
            key={i}
            className="bg-phos/10 px-0.5 text-ink-hi underline decoration-phos underline-offset-2"
          >
            {segment.text}
          </mark>
        ) : (
          <span key={i}>{segment.text}</span>
        )
      )}
    </p>
  );
}

/**
 * One document as a box ("Boxes, not blocks"): its agency or chamber, type
 * and date over the title; where a comment period is open, the days left
 * as its figure and "Comment" as its command; for a search, the passage
 * that matched (the document's own words, terms marked); and the agency's
 * abstract, whole, behind "More".
 */
export function DocumentCard({ result, query }: { result: ExploreResult; query: string }) {
  const detailHref = query
    ? `${exploreHref(result)}?q=${encodeURIComponent(query)}`
    : exploreHref(result);
  const commentOpen = !!result.commentUrl && isCommentPeriodOpen(result.commentsCloseOn);
  const source =
    result.chamber === "Regulatory" && result.agencyName
      ? result.agencyName
      : chamberLabel(result.chamber);
  // The keyword channel's excerpt says why a document came back; a
  // semantic-only result's "snippet" is its summary, kept for "More".
  const passage =
    query && result.snippet && result.snippet !== result.summary ? result.snippet : "";
  const meta = [
    result.politicianName,
    (result.citedByCount ?? 0) > 0 ? `Cited by ${result.citedByCount}` : "",
    (result.duplicateCount ?? 0) > 0
      ? `+${result.duplicateCount} duplicate${result.duplicateCount !== 1 ? "s" : ""}`
      : "",
  ].filter(Boolean);
  return (
    <RecordBox
      compact
      tone={commentOpen ? "deadline" : "neutral"}
      // Who, what, when and how cited in one line: a line of its own under
      // each result made the list longer than the cards it replaced.
      kicker={[
        source,
        result.docType || "Document",
        result.date ? formatDate(result.date) : "",
        ...meta,
      ]
        .filter(Boolean)
        .join(" · ")}
      title={result.title}
      href={detailHref}
      figure={
        commentOpen && result.commentWindow ? { type: "countdown", ...result.commentWindow } : null
      }
      action={
        commentOpen
          ? {
              label: "Comment",
              href: `${detailHref}#comment`,
              ariaLabel: `Comment on ${result.title}`,
            }
          : undefined
      }
      more={
        result.summary
          ? {
              label: result.chamber === "Regulatory" ? "the agency's summary" : "the summary",
              children: (
                <figure>
                  <blockquote>
                    <p className="leading-relaxed">{result.summary}</p>
                  </blockquote>
                  <figcaption className="mt-1 text-ink-min">
                    {result.chamber === "Regulatory"
                      ? `${result.agencyName ? `${result.agencyName}, in the` : "The"} Federal Register`
                      : result.source || source}
                  </figcaption>
                </figure>
              ),
            }
          : null
      }
    >
      {passage && <Snippet text={passage} />}
    </RecordBox>
  );
}

/** What the documents hold, by type, from when: each feed's ingest started
 * on a date of its own, so a document older than that isn't here. */
function CoverageWindows({ stats }: { stats: ExploreStats | null }) {
  const windows = stats?.coverage ?? [];
  if (windows.length === 0) return null;
  return (
    <details className="mt-3 font-mono text-xs text-ink-lo">
      <summary className="inline-flex min-h-6 cursor-pointer items-center hover:text-phos">
        What the documents cover, and from when
      </summary>
      <ul className="mt-2 space-y-1">
        {windows.map((w) => (
          <li key={w.label}>
            {w.label}: {formatDate(w.from)} to {formatDate(w.to)} ·{" "}
            <span className="tabular-nums">{w.count.toLocaleString("en-US")}</span>
          </li>
        ))}
        <li className="text-ink-min">
          Older documents are at the Federal Register, GovInfo and the Supreme Court.
        </li>
      </ul>
    </details>
  );
}

/** Identifiers the search names that nothing here is: why, and where the
 * official copy is. */
function NotHeldNote({ notes, cited }: { notes: ExploreNotHeld[]; cited: boolean }) {
  return (
    <div role="status" className="mb-4 border border-signal-amber/30 bg-signal-amber/5 px-3 py-2">
      {notes.map((n) => (
        <p key={n.identifier} className="text-sm text-ink-hi">
          {n.sentence}{" "}
          <a
            href={n.url}
            className="text-signal-amber underline underline-offset-4 hover:text-phos"
          >
            Find {n.identifier} at {n.source}
          </a>
          .
        </p>
      ))}
      {cited && <p className="mt-1 text-xs text-ink-lo">The results below cite it.</p>}
    </div>
  );
}

/** The index's counts, for the masthead. */
export function IndexCounts({ stats }: { stats: ExploreStats | null }) {
  const docs = stats?.totalDocuments ?? 0;
  if (docs === 0) return null;
  const open = stats?.openForComment ?? 0;
  return (
    <div className="text-right font-mono text-xs">
      <p className="tabular-nums text-ink-lo">{docs.toLocaleString("en-US")} documents indexed</p>
      {open > 0 && <p className="tabular-nums text-signal-cyan">{open} open for comment</p>}
    </div>
  );
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * Site search's Documents filter: what Explore's search page was. The
 * results are Explore's hybrid ranking (`exploreParams`), read by the
 * server for the address's parameters, so a link, a refresh or a page
 * without JavaScript shows the same list. Filters are a GET form
 * (DocumentFilters): every change is a new address.
 */
export default function DocumentResults({
  d,
  stats,
  resp,
  failed,
  committeeTotal,
}: {
  d: DocumentQuery;
  stats: ExploreStats | null;
  /** Null when nothing was asked, or the request failed (`failed`). */
  resp: ExploreResponse | null;
  failed: boolean;
  /** Daily Digest committee meetings and reports matching the words. */
  committeeTotal: number;
}) {
  const asked = asksForDocuments(d);
  const q = d.q.length >= 2 ? d.q : "";
  const indexEmpty = Boolean(resp?.indexEmpty);
  const results = indexEmpty ? [] : (resp?.results ?? []);
  const semanticDown = !indexEmpty && Boolean(resp?.semanticUnavailable);
  const politicianName = d.politicianId
    ? results.find((r) => r.politicianId === d.politicianId)?.politicianName || "one member"
    : "";
  const agencyName = d.agency
    ? stats?.agencies?.find((a) => a.slug === d.agency)?.name ||
      results.find((r) => r.agencySlug === d.agency)?.agencyName ||
      "one agency"
    : "";
  const sortedBy =
    !q && !d.politicianId && (!d.agency || d.commentOpen)
      ? "rules first, then readers"
      : !q || d.sort === "date"
        ? "newest first"
        : "relevance";
  const everyone = documentsHref({
    q: q || undefined,
    agency: d.agency || undefined,
    comment: d.commentOpen ? "open" : undefined,
    type: d.type || undefined,
    chamber: d.chamber || undefined,
  });

  return (
    <div>
      <DocumentFilters d={d} stats={stats} agencyName={agencyName} />
      <CoverageWindows stats={stats} />

      {d.politicianId && (
        <p className="mt-4 flex flex-wrap items-center gap-3 text-xs">
          <span className="text-ink-lo">
            Only documents from <span className="text-ink-hi">{politicianName}</span>
          </span>
          <Link
            prefetch={false}
            href={everyone}
            className="inline-flex min-h-6 items-center font-monost text-signal-cyan hover:text-phos"
          >
            Search everyone
          </Link>
        </p>
      )}

      {/* A filter that changes the list says so (WCAG 4.1.3): the
          controls submit on change and focus stays on them. */}
      <p role="status" className="sr-only">
        {asked && !failed && !indexEmpty
          ? `${plural(results.length, "document", "documents")} listed`
          : ""}
      </p>

      <div className="mt-6">
        {!asked && (
          <div>
            <p className="mb-3 text-xs text-ink-lo">Try searching for:</p>
            <ul className="flex flex-wrap gap-2">
              {SUGGESTED_QUERIES.map((s) => (
                <li key={s}>
                  <Link
                    prefetch={false}
                    href={documentsHref({ q: s })}
                    className="inline-flex min-h-6 items-center border border-white/15 px-3 py-1.5 text-xs text-ink-lo hover:text-phos"
                  >
                    {s}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}

        {asked && (failed || indexEmpty) && (
          <p role="alert" className="py-8 text-center text-base text-ink-lo">
            {indexEmpty
              ? "The search index is still being built. This happens right after a data refresh: please check back in a few minutes."
              : "The document search didn't answer just now. Try again in a moment."}
          </p>
        )}

        {semanticDown && results.length > 0 && (
          <p
            role="status"
            className="mb-4 border border-signal-amber/30 bg-signal-amber/5 px-3 py-2 text-xs text-signal-amber"
          >
            Showing keyword matches only: the meaning-based index is rebuilding after a data
            refresh. Searches by topic will return more once it finishes, usually within a few
            minutes.
          </p>
        )}

        {(resp?.notHeld?.length ?? 0) > 0 && (
          <NotHeldNote notes={resp!.notHeld!} cited={results.length > 0} />
        )}

        {q && !d.politicianId && committeeTotal > 0 && (
          <Link
            href={`/congress/committees?q=${encodeURIComponent(q)}#committee-results`}
            className="mb-4 block border border-white/15 px-4 py-3 text-sm text-ink-hi hover:border-phos/40"
          >
            <span className="font-mono text-xs text-ink-min">Committees</span> {committeeTotal}{" "}
            {committeeTotal === 1
              ? "committee meeting or report"
              : "committee meetings and reports"}{" "}
            in the Daily Digest match &ldquo;{q}&rdquo; →
          </Link>
        )}

        {asked && results.length > 0 && (
          <section aria-labelledby="document-results">
            <h2 id="document-results" className="mb-4 text-xs font-normal text-ink-lo">
              {q ? (
                <>
                  {plural(results.length, "document", "documents")} for &ldquo;{q}&rdquo;
                </>
              ) : d.politicianId ? (
                <>
                  {results.length === DOCUMENT_LIMIT ? "The most recent " : ""}
                  {plural(results.length, "document", "documents")} from {politicianName}
                </>
              ) : d.agency && !d.commentOpen ? (
                <>
                  {results.length === DOCUMENT_LIMIT ? "The most recent " : ""}
                  {plural(results.length, "document", "documents")} from {agencyName}
                </>
              ) : (
                <>
                  {results.length === DOCUMENT_LIMIT ? "The first " : ""}
                  {plural(results.length, "open comment period", "open comment periods")}
                </>
              )}
              {d.commentOpen && (q || d.politicianId) && (
                <span className="ml-2 text-phos-mid">· open for comment only</span>
              )}
              {d.agency && (q || d.politicianId || d.commentOpen) && (
                <span className="ml-2 text-ink-lo">· {agencyName} only</span>
              )}
              <span className="ml-2 text-ink-min">· sorted by {sortedBy}</span>
            </h2>
            <BoxGrid columns={2}>
              {results.map((r) => (
                <DocumentCard key={r.id} result={r} query={q} />
              ))}
            </BoxGrid>
            {resp?.paperwork && (
              <PaperworkFold count={resp.paperwork.count} shown={resp.paperwork.results.length}>
                <BoxGrid columns={2} className="mt-3">
                  {resp.paperwork.results.map((r) => (
                    <DocumentCard key={r.id} result={r} query={q} />
                  ))}
                </BoxGrid>
              </PaperworkFold>
            )}
          </section>
        )}

        {asked && !failed && !indexEmpty && results.length === 0 && (
          <div className="py-12 text-center">
            <p className="mb-2 text-base text-ink-lo">
              {q
                ? `No documents found for “${q}”`
                : d.politicianId
                  ? "No documents on record for this member"
                  : d.agency && !d.commentOpen
                    ? "No documents on record for this agency"
                    : "No comment periods are open right now"}
            </p>
            {!resp?.notHeld?.length && (
              <p className="text-xs text-ink-lo">Try a broader search term or other filters.</p>
            )}
          </div>
        )}
      </div>

      <p className="mx-auto mt-12 max-w-lg text-center font-sans text-xs text-ink-lo">
        Data sourced from the Congressional Record (GovInfo API), the Federal Register
        (federalregister.gov), and Supreme Court opinions (supremecourt.gov via Oyez). Comment links
        go directly to regulations.gov.
      </p>
    </div>
  );
}
