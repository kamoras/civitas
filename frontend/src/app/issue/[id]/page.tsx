import { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import { ActionIssue } from "@/types/action";
import { usableRecord } from "@/lib/ssrPayload";
import { commentPeriodToday, formatUtcDate, restatesTitle } from "@/lib/formatting";
import { ACTION_CENTER_HREF } from "@/lib/routes";
import { countIsOfficial, factsHeading, factsSectionId, factsShareLabel } from "@/lib/developing";
import {
  Coverage,
  DevelopingDisclosure,
  IssueImage,
  IssueMeta,
  IssueTags,
  SECTION_HEADING,
  SourceList,
  SummarySource,
  TEXT_LINK,
  WhatYouCanDo,
} from "@/components/action/IssueEnrichment";
import ShareButtons from "@/components/action/ShareButtons";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { articleJsonLd } from "@/lib/seo";
import JsonLd from "@/components/seo/JsonLd";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

interface Retraction {
  date: string;
  reason: string;
}

/** The issue; or, for one Civitas withdrew (410, backend/app/data/
 * retractions.json), the date and reason; or null. */
async function fetchIssueOrRetraction(
  id: string
): Promise<{ issue: ActionIssue | null; retraction: Retraction | null }> {
  const res = await fetch(`${BACKEND}/api/action/issues/${encodeURIComponent(id)}`, {
    next: { revalidate: 300 },
  });
  if (res.status === 410) {
    const detail = (await res.json())?.detail;
    return {
      issue: null,
      retraction: detail?.reason ? { date: detail.date, reason: detail.reason } : null,
    };
  }
  if (res.status === 404) return { issue: null, retraction: null };
  // An outage is not a missing issue: a 404 marked noindex would drop a real
  // page from search for as long as the backend was down (fetchRecord).
  if (!res.ok) throw new Error(`issue ${id}: HTTP ${res.status}`);
  return { issue: usableRecord<ActionIssue>(await res.json(), "id", "title"), retraction: null };
}

function Withdrawn({ retraction }: { retraction: Retraction }) {
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
            Withdrawn {retraction.date}
          </p>
          <h1 className="font-display text-3xl font-extrabold text-ink-hi">
            This issue was withdrawn
          </h1>
          <p className="leading-relaxed text-ink">{retraction.reason}</p>
          <p className="text-sm text-ink-lo">
            Every withdrawal is listed, with its reason, in the public retraction log in the Civitas
            source code.{" "}
            <Link
              href={ACTION_CENTER_HREF}
              className="underline decoration-white/30 underline-offset-4 hover:text-phos"
            >
              Current issues
            </Link>
          </p>
        </div>
      </main>
      <Footer />
    </div>
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const { issue, retraction } = await fetchIssueOrRetraction(id);

  if (retraction) {
    return pageMetadata({
      title: "Issue withdrawn",
      description: retraction.reason,
      path: `/issue/${encodeURIComponent(id)}`,
      noindex: true,
    });
  }
  if (!issue) {
    return pageMetadata({
      title: "Issue not found",
      description: "No record for this issue.",
      path: `/issue/${encodeURIComponent(id)}`,
      noindex: true,
    });
  }

  // Canonical from the record: public ids resolve case-insensitively
  // (from_public_id), so the request's spelling isn't necessarily the one
  // to index.
  const path = `/issue/${issue.publicId}`;
  const ogImage = absoluteUrl(`/api/og?issue=${issue.publicId}`);
  return pageMetadata({
    title: issue.title,
    description: issue.summary || "Track what Congress is doing, and what you can do about it.",
    path,
    type: "article",
    images: [{ url: ogImage, width: 1200, height: 630, alt: issue.title }],
    // A developing issue is drafted from a primary source before any press
    // coverage confirms it, and may expire unconfirmed. It stays readable
    // here, but isn't offered to search engines as a finished story.
    noindex: issue.status === "developing",
  });
}

export default async function IssuePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { issue, retraction } = await fetchIssueOrRetraction(id);
  if (retraction) return <Withdrawn retraction={retraction} />;

  // A real 404, not a 200 page that says "not found": search engines
  // index the latter as a thin duplicate of every other missing id.
  if (!issue) notFound();

  const paragraphs = issue.fullStory
    ? issue.fullStory.split(/\n\n+/).filter((p) => p.trim())
    : null;

  // Resolved once, on the server, so a comment period reads as open/closed
  // identically before and after hydration. In the comment-deadline zone,
  // the same day the Action Center and the backend count comment periods in.
  const today = commentPeriodToday();
  const shareUrl = absoluteUrl(`/issue/${issue.publicId}`);
  const factsId = factsSectionId(issue);

  return (
    <ShareSubjectProvider
      subject={{ title: issue.title, subtitle: formatUtcDate(issue.date), url: shareUrl }}
    >
      {issue.status !== "developing" && <JsonLd data={articleJsonLd(issue)} />}
      <Navbar />
      <main
        id="main-content"
        tabIndex={-1}
        className="min-h-screen px-4 pb-16 pt-[var(--header-clearance)] text-ink"
      >
        <div className="relative z-10 mx-auto max-w-3xl">
          <Link
            href={ACTION_CENTER_HREF}
            className="font-mono text-xs uppercase tracking-[0.14em] text-ink-lo hover:text-ink-hi"
          >
            ← Action Center
          </Link>

          {/* Header */}
          <header
            id="summary"
            {...{ [SHARE_SECTION_ATTR]: "summary" }}
            className="mt-6 scroll-mt-[var(--header-clearance)] border-b-3 border-ink-min/60 pb-6"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              {/* issueDateLabel inside: firstSurfaced, then "updated" when the
                  story was re-matched on a later day. */}
              <IssueMeta issue={issue} />
              {/* The header names the issue itself: no title strip. */}
              <ShareSectionButton label="Issue summary" withStrip={false} />
            </div>
            <h1 className="mt-3 text-balance font-display text-3xl font-extrabold leading-tight tracking-[-0.01em] text-ink-hi sm:text-4xl">
              {issue.title}
            </h1>
            {!restatesTitle(issue.title, issue.summary) && (
              <p className="mt-4 font-display text-base leading-relaxed text-ink sm:text-[17px]">
                {issue.summary}
                <SummarySource issue={issue} />
              </p>
            )}
            {issue.status === "developing" && (
              <div className="mt-3">
                <DevelopingDisclosure
                  sourceType={issue.sourceType}
                  countOfficial={countIsOfficial(issue)}
                />
              </div>
            )}
            <IssueTags issue={issue} className="mt-4" />
          </header>

          <div className="mt-8">
            <IssueImage issue={issue} />
          </div>

          {/* Full story */}
          {paragraphs ? (
            <article className="mb-4 mt-8 space-y-5 font-display text-base leading-relaxed text-ink">
              {paragraphs.map((para, i) => {
                // claims.build_story's only structure: "## Outlet" above
                // that outlet's verbatim claims. Everything else is a
                // source's own words and renders as written — no other
                // markdown is interpreted, since a quote is not markup.
                if (para.startsWith("## ")) {
                  return (
                    <h2
                      key={i}
                      className="mt-10 border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min first:mt-0"
                    >
                      {para.slice(3)}
                    </h2>
                  );
                }
                return <p key={i}>{para}</p>;
              })}
            </article>
          ) : null}

          {/* Media coverage: lines quoted from the sources, each with its
              outlet — or, for an election-results issue, the count. */}
          {issue.facts?.length > 0 && (
            <div
              id={factsId}
              {...{ [SHARE_SECTION_ATTR]: factsId }}
              className="scroll-mt-[var(--header-clearance)]"
            >
              <Coverage
                issue={issue}
                className="mt-10"
                heading={
                  <div className={SECTION_HEADING}>
                    <h2>{factsHeading(issue)}</h2>
                    <ShareSectionButton label={factsShareLabel(issue)} />
                  </div>
                }
              />
            </div>
          )}

          <WhatYouCanDo issue={issue} today={today} headingLevel="h2" className="mt-10" />

          <div className="mt-10 flex flex-col gap-3 border-t border-white/[0.07] pt-4">
            <SourceList issue={issue} />
            <ShareButtons issue={issue} shareUrl={shareUrl} imageShare={false} />
          </div>

          <div className="mt-10 border-t-3 border-ink-min/60 pt-5">
            <Link href={ACTION_CENTER_HREF} className={TEXT_LINK}>
              ← Back to the Action Center
            </Link>
          </div>
        </div>
      </main>
      <Footer />
      <BackToTop />
    </ShareSubjectProvider>
  );
}
