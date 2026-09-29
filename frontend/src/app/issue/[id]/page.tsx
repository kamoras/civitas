import { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import { ActionIssue } from "@/types/action";
import { usableRecord } from "@/lib/ssrPayload";
import { formatUtcDate, isNewFact } from "@/lib/formatting";
import { ACTION_CENTER_HREF } from "@/lib/routes";
import { countIsOfficial, factsAreTheCount, factsHeading, factsSectionId } from "@/lib/developing";
import { formatEasternTime } from "@/lib/results";
import {
  PolicyBadge,
  MonitorChips,
  NewFactTag,
  IssueImage,
} from "@/components/action/IssueEnrichment";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { articleJsonLd } from "@/lib/seo";
import JsonLd from "@/components/seo/JsonLd";
import IssueActions from "./IssueActions";
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
  try {
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
    if (!res.ok) return { issue: null, retraction: null };
    return { issue: usableRecord<ActionIssue>(await res.json(), "id", "title"), retraction: null };
  } catch {
    return { issue: null, retraction: null };
  }
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
    description: issue.summary || "Track what Congress is doing — and what you can do about it.",
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

  // Resolved once, on the server, and handed to the client panel so a comment
  // period reads as open/closed identically before and after hydration.
  const renderedAt = new Date();
  const today = renderedAt.toISOString().slice(0, 10);
  const shareUrl = absoluteUrl(`/issue/${issue.publicId}`);
  const factsId = factsSectionId(issue);
  // A count issue's facts are the count as this page read it, and the issue
  // carries no time of its own (only a date), so the section says when that
  // was and whether the state calls it official — inside the section, so a
  // shared image of it says so too, not just the frame's capture day.
  // When Civitas read these figures (the backend's countAsOf); an older
  // backend sends none, and then the render time stands in, labelled as
  // when this page read it.
  const countReadAt = factsAreTheCount(issue)
    ? formatEasternTime(issue.countAsOf ?? renderedAt.toISOString())
    : null;

  return (
    <ShareSubjectProvider
      subject={{ title: issue.title, subtitle: formatUtcDate(issue.date), url: shareUrl }}
    >
      {issue.status !== "developing" && <JsonLd data={articleJsonLd(issue)} />}
      <Navbar />
      <main
        id="main-content"
        tabIndex={-1}
        className="min-h-screen text-ink-hi font-mono pt-[var(--header-clearance)] pb-16 px-4"
      >
        <div className="max-w-3xl mx-auto relative z-10">
          {/* Nav */}
          <div className="mb-8">
            <Link
              href={ACTION_CENTER_HREF}
              className="text-xs text-ink-lo hover:text-phos transition-colors"
            >
              ← ACTION CENTER
            </Link>
          </div>

          {/* Header */}
          <header
            id="summary"
            {...{ [SHARE_SECTION_ATTR]: "summary" }}
            className="mb-8 scroll-mt-[var(--header-clearance)] space-y-3 border-b border-white/[0.07] pb-8"
          >
            {issue.policyAreas?.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {issue.policyAreas.map((area) => (
                  <PolicyBadge key={area} area={area} />
                ))}
              </div>
            )}
            <h1 className="text-xl leading-tight text-ink-hi">{issue.title}</h1>
            <p className="text-base text-ink leading-relaxed">{issue.summary}</p>
            <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-ink-min">
              <span>
                {/* firstSurfaced missing (not merely equal to date) falls back to
                    date alone — see issueDateLabel's docstring in lib/formatting.ts
                    for why that's a real case, not just a hypothetical. */}
                {formatUtcDate(issue.firstSurfaced || issue.date)}
                {issue.firstSurfaced &&
                  issue.firstSurfaced !== issue.date &&
                  ` · updated ${formatUtcDate(issue.date)}`}
              </span>
              {/* The header names the issue itself: no title strip. */}
              <ShareSectionButton label="Issue summary" withStrip={false} />
            </div>
            <MonitorChips slugs={issue.relatedMonitorSlugs} />
          </header>

          <IssueImage issue={issue} />

          {/* Full story */}
          {paragraphs ? (
            <article className="space-y-5 text-sm text-ink leading-relaxed mb-12">
              {paragraphs.map((para, i) => {
                // claims.build_story's only structure: "## Outlet" above
                // that outlet's verbatim claims. Everything else is a
                // source's own words and renders as written — no other
                // markdown is interpreted, since a quote is not markup.
                if (para.startsWith("## ")) {
                  return (
                    <h2
                      key={i}
                      className="text-base text-ink-hi font-bold mt-8 first:mt-0 border-l-2 border-white/15 pl-3"
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
            <section
              id={factsId}
              {...{ [SHARE_SECTION_ATTR]: factsId }}
              className="mb-10 scroll-mt-[var(--header-clearance)]"
            >
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-xs text-ink-min tracking-widest uppercase">
                  {factsHeading(issue)}
                </h2>
                <ShareSectionButton label={factsHeading(issue)} />
              </div>
              <ul className="space-y-3">
                {issue.facts.map((fact, i) => (
                  <li key={i} className="flex gap-3 text-sm text-ink">
                    <span className="text-ink-min shrink-0 mt-0.5">▸</span>
                    <span>
                      {fact}
                      {issue.factSources?.[i] && (
                        <span className="ml-2 font-mono text-xs text-ink-min">
                          {issue.factSources[i]}
                        </span>
                      )}
                      {isNewFact(issue.newFacts, fact) && <NewFactTag />}
                    </span>
                  </li>
                ))}
              </ul>
              {countReadAt && (
                <p className="mt-4 text-xs text-ink-min">
                  {countIsOfficial(issue) ? (
                    <span className="text-ink-hi">OFFICIAL COUNT</span>
                  ) : (
                    <span className="text-signal-amber">NOT FINAL</span>
                  )}{" "}
                  · the count as of {countReadAt},{" "}
                  {issue.countAsOf ? "when Civitas read it" : "when this page read it"}. The
                  state&apos;s own results site has the current count.
                </p>
              )}
            </section>
          )}

          <IssueActions issue={issue} today={today} shareUrl={shareUrl} />

          {/* Back */}
          <div className="pt-8 border-t border-white/[0.07]">
            <Link
              href={ACTION_CENTER_HREF}
              className="text-xs text-ink-lo hover:text-phos transition-colors"
            >
              ← Back to Action Center
            </Link>
          </div>
        </div>
      </main>
      <Footer />
      <BackToTop />
    </ShareSubjectProvider>
  );
}
