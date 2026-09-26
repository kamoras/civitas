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
import { PolicyBadge, MonitorChips, NewFactTag, IssueImage } from "@/components/action/IssueEnrichment";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { articleJsonLd } from "@/lib/seo";
import JsonLd from "@/components/seo/JsonLd";
import IssueActions from "./IssueActions";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

async function fetchIssue(id: string): Promise<ActionIssue | null> {
  try {
    const res = await fetch(`${BACKEND}/api/action/issues/${id}`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    return usableRecord<ActionIssue>(await res.json(), "id", "title");
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const issue = await fetchIssue(id);

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
  });
}

export default async function IssuePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const issue = await fetchIssue(id);

  // A real 404, not a 200 page that says "not found": search engines
  // index the latter as a thin duplicate of every other missing id.
  if (!issue) notFound();

  const paragraphs = issue.fullStory
    ? issue.fullStory.split(/\n\n+/).filter((p) => p.trim())
    : null;

  // Resolved once, on the server, and handed to the client panel so a comment
  // period reads as open/closed identically before and after hydration.
  const today = new Date().toISOString().slice(0, 10);

  return (
    <>
      <JsonLd data={articleJsonLd(issue)} />
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
          <header className="mb-8 space-y-3 border-b border-white/[0.07] pb-8">
            {issue.policyAreas?.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {issue.policyAreas.map((area) => (
                  <PolicyBadge key={area} area={area} />
                ))}
              </div>
            )}
            <h1 className="text-xl leading-tight text-ink-hi">{issue.title}</h1>
            <p className="text-base text-ink leading-relaxed">{issue.summary}</p>
            <div className="text-xs text-ink-min">
              {/* firstSurfaced missing (not merely equal to date) falls back to
                  date alone — see issueDateLabel's docstring in lib/formatting.ts
                  for why that's a real case, not just a hypothetical. */}
              {formatUtcDate(issue.firstSurfaced || issue.date)}
              {issue.firstSurfaced &&
                issue.firstSurfaced !== issue.date &&
                ` · updated ${formatUtcDate(issue.date)}`}
            </div>
            <MonitorChips slugs={issue.relatedMonitorSlugs} />
          </header>

          <IssueImage issue={issue} />

          {/* Full story */}
          {paragraphs ? (
            <article className="space-y-5 text-sm text-ink leading-relaxed mb-12">
              {paragraphs.map((para, i) => {
                // Detect markdown-style headers (# or ## Heading)
                if (para.startsWith("# ") || para.startsWith("## ")) {
                  return (
                    <h2
                      key={i}
                      className="text-base text-ink-hi font-bold mt-8 first:mt-0 border-l-2 border-white/15 pl-3"
                    >
                      {para.replace(/^#+\s+/, "")}
                    </h2>
                  );
                }
                // Bold paragraph: starts and ends with **
                if (para.startsWith("**") && para.endsWith("**") && para.length > 4) {
                  const stripped = para.slice(2, -2);
                  return (
                    <p key={i} className="text-ink-hi font-medium">
                      {stripped}
                    </p>
                  );
                }
                return <p key={i}>{para}</p>;
              })}
            </article>
          ) : (
            <div className="mb-12 py-10 border border-white/[0.07] text-center text-ink-min text-sm">
              Full story not yet available. Check back soon.
            </div>
          )}

          {/* Key facts */}
          {issue.facts?.length > 0 && (
            <section className="mb-10">
              <h2 className="text-xs text-ink-min mb-4 tracking-widest">KEY FACTS</h2>
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
            </section>
          )}

          <IssueActions issue={issue} today={today} shareUrl={absoluteUrl(`/issue/${issue.publicId}`)} />

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
    </>
  );
}
