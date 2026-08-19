"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import SenatorCard from "@/components/checker/SenatorCard";
import { PresidentCard } from "@/components/president/PresidentClient";
import { JusticeCard } from "@/components/justice/JusticeClient";
import { formerOfficeNotice } from "@/lib/officeStatus";
import type { PoliticianProfile, GovernmentDoc } from "@/types/politicians";
import type { Senator } from "@/types/senator";
import type { President } from "@/types/president";
import type { Justice } from "@/types/justice";

const DOC_TYPE_LABELS: Record<string, string> = {
  "Senate Floor Speech": "FLOOR SPEECH",
  "House Floor Speech": "FLOOR SPEECH",
  "Executive Order": "EXEC ORDER",
  Proclamation: "PROCLAMATION",
  "Supreme Court Opinion": "COURT OPINION",
  "Presidential Memorandum": "MEMO",
};

function branchLabel(branch: string) {
  const map: Record<string, string> = {
    senate: "SENATE",
    house: "HOUSE",
    president: "EXECUTIVE",
    scotus: "JUDICIAL",
  };
  return map[branch] ?? branch.toUpperCase();
}

function DocRow({ doc }: { doc: GovernmentDoc }) {
  const typeLabel = DOC_TYPE_LABELS[doc.docType] ?? doc.docType.toUpperCase();
  return (
    <div className="flex items-start gap-3 py-2 border-b border-white/[0.07] last:border-0">
      <span className="font-mono text-xs text-ink-min tracking-widest shrink-0 mt-0.5 w-24">
        {doc.date ?? "—"}
      </span>
      <div className="flex-1 min-w-0">
        <span className="font-mono text-xs text-ink-lo tracking-widest mr-2">[{typeLabel}]</span>
        {doc.url ? (
          <a
            href={doc.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-xs text-ink hover:text-phos transition-colors"
          >
            {doc.title}
          </a>
        ) : (
          <span className="font-mono text-xs text-ink-lo">{doc.title}</span>
        )}
      </div>
    </div>
  );
}

function SectionBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-6">
      <TerminalTitlebar title={title} />
      <div className="border border-t-0 border-white/[0.07] bg-surface-base p-4">{children}</div>
    </div>
  );
}

export default function PoliticianProfileClient({ profile }: { profile: PoliticianProfile }) {
  const { identity, branch, activeIssues, governmentRecord, scorecard } = profile;

  // Justices carry `isActive`; every other branch carries `isCurrent`.
  const hasLeftOffice =
    branch === "scotus" ? identity.isActive === false : identity.isCurrent === false;
  const formerOffice = hasLeftOffice ? formerOfficeNotice({ branch, ...identity }) : null;

  return (
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-4xl mx-auto">
          {/* Breadcrumb */}
          <div className="mb-6 font-mono text-xs text-ink-min">
            <Link href="/politicians" className="hover:text-phos transition-colors">
              ← POLITICIANS
            </Link>
            <span className="mx-2">/</span>
            <span className="text-ink-lo">{branchLabel(branch)}</span>
          </div>

          {/* Left-office banner — wording is branch-specific, see officeStatus.ts */}
          {formerOffice && (
            <div className="mb-6 border border-signal-magenta/40 bg-signal-magenta/10 px-4 py-3">
              <p className="font-mono text-xs text-signal-magenta tracking-widest uppercase mb-1">
                {formerOffice.label}
              </p>
              <p className="font-mono text-xs text-ink-lo">{formerOffice.detail}</p>
            </div>
          )}

          {/* Identity and scorecard first.

              This block used to sit third, below the Action Center issues and
              the government-record list, so a profile opened on a stack of
              news cards and the reader had to scroll past them to find out
              whose page they were on. Who this is, and how they score, is the
              page; what is trending that mentions them is context for it. */}
          {/* Scorecard */}
          {/* Gated on `scorecard`, not on `hasScorecard`.

              The two come from different places on the API side: hasScorecard
              is `overall is not None` off the member row, while scorecard is
              built by a helper whose body is wrapped in a bare
              `except Exception: return None`. So any failure building the
              detail — a schema mismatch, a half-written pipeline row — yields
              hasScorecard:true with scorecard:null, and this page used to fall
              through BOTH branches and render a breadcrumb over an empty
              screen: no name, no scores, no explanation. Reproduced on a
              president. Rendering off the object that is actually needed means
              the fallback below always catches it. */}
          {scorecard && (
            <div className="mb-6">
              {(branch === "senate" || branch === "house") && (
                <SenatorCard
                  senator={scorecard as unknown as Senator}
                  chamber={branch}
                  thumbnailUrl={identity.thumbnailUrl}
                  district={identity.district}
                  stateName={identity.stateName}
                  isCurrent={identity.isCurrent}
                  leadershipTitle={identity.leadershipTitle}
                  titleAs="h1"
                />
              )}
              {branch === "president" && (
                <PresidentCard president={scorecard as unknown as President} titleAs="h1" />
              )}
              {branch === "scotus" && (
                <JusticeCard justice={scorecard as unknown as Justice} titleAs="h1" />
              )}

              {(branch === "senate" || branch === "house") && identity.state && (
                <div className="mt-3 text-center">
                  <Link
                    href={`/politicians?branch=${branch}&state=${identity.state}`}
                    className="font-mono text-xs text-ink-min hover:text-phos transition-colors tracking-widest"
                  >
                    COMPARE ALL {identity.stateName ?? identity.state}{" "}
                    {branch === "senate" ? "SENATORS" : "REPRESENTATIVES"} →
                  </Link>
                </div>
              )}
            </div>
          )}

          {/* Active Issues */}
          {activeIssues.length > 0 && (
            <SectionBlock title="In the Action Center">
              <p className="font-mono text-xs text-ink-min tracking-widest mb-3">
                CURRENTLY ACTIVE IN {activeIssues.length} ISSUE
                {activeIssues.length !== 1 ? "S" : ""}
              </p>
              <div className="space-y-3">
                {activeIssues.map((issue) => (
                  <div key={issue.id} className="border border-white/[0.07] bg-surface-base p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-mono text-xs text-ink-min">RANK #{issue.rank}</span>
                          <span className="font-mono text-xs text-ink-min">{issue.date}</span>
                        </div>
                        <p className="mb-1 font-sans text-base text-ink-hi">{issue.title}</p>
                        {issue.summary && (
                          <p className="line-clamp-2 font-sans text-xs text-ink-lo">
                            {issue.summary}
                          </p>
                        )}
                      </div>
                      <Link
                        href={`/issue/${issue.id}`}
                        className="shrink-0 font-mono text-xs text-ink-lo hover:text-phos transition-colors tracking-widest whitespace-nowrap"
                      >
                        VIEW →
                      </Link>
                    </div>
                  </div>
                ))}
              </div>
            </SectionBlock>
          )}

          {/* Government Record */}
          {governmentRecord.totalDocs > 0 && (
            <SectionBlock title="Government record">
              <p className="font-mono text-xs text-ink-min tracking-widest mb-3">
                {governmentRecord.totalDocs} DOCUMENT{governmentRecord.totalDocs !== 1 ? "S" : ""}{" "}
                ON PUBLIC RECORD · VERBATIM SOURCE LINKS
              </p>
              {governmentRecord.recentDocs.map((doc) => (
                <DocRow key={doc.id} doc={doc} />
              ))}
              {governmentRecord.totalDocs > 5 && (
                <Link
                  href={`/explore?politician_id=${profile.id}`}
                  className="block mt-3 font-mono text-xs text-ink-min hover:text-phos transition-colors tracking-widest"
                >
                  VIEW ALL {governmentRecord.totalDocs} DOCUMENTS →
                </Link>
              )}
            </SectionBlock>
          )}

          {/* Committee Memberships */}
          {(branch === "senate" || branch === "house") &&
            identity.committees &&
            identity.committees.length > 0 && (
              <SectionBlock title="Committee assignments">
                <div className="space-y-1.5">
                  {identity.committees.map((c, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between gap-3 py-1 border-b border-white/[0.07] last:border-0"
                    >
                      <span className="font-mono text-xs text-ink">{c.committeeName}</span>
                      {c.title && (
                        <span className="font-mono text-xs tracking-widest border border-white/15 text-ink-lo px-1.5 py-0.5 shrink-0">
                          {c.title.toUpperCase()}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </SectionBlock>
            )}

          {!scorecard && (
            <SectionBlock title="Scorecard">
              {/* No card header will render below to carry identity, so
                  show a minimal one here — otherwise a not-yet-scored
                  official's page has no name/photo/party anywhere on it. */}
              <div className="flex items-center gap-3 mb-4">
                {identity.thumbnailUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element -- external, varied politician-photo hosts
                  <img
                    src={identity.thumbnailUrl}
                    alt={identity.name}
                    className="w-12 h-12 object-cover border border-white/[0.07] shrink-0"
                  />
                ) : null}
                <div>
                  {/* h1, not a <p>: on a not-yet-scored official this block is
                      the only identity on the page, so it is the page title.
                      The scored path gets its h1 from the card's `titleAs`. */}
                  <h1 className="font-display font-semibold text-base text-ink-hi">
                    {identity.name}
                  </h1>
                  <p className="font-mono text-xs text-ink-min tracking-widest">
                    {identity.role}
                    {identity.state ? ` · ${identity.stateName ?? identity.state}` : ""}
                  </p>
                </div>
              </div>
              <p className="font-mono text-xs text-ink-min tracking-widest text-center py-4">
                SCORECARD NOT YET GENERATED — CHECK BACK AFTER NEXT PIPELINE RUN
              </p>
            </SectionBlock>
          )}
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
