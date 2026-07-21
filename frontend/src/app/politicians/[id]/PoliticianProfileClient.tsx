"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import SenatorCard from "@/components/checker/SenatorCard";
import { PresidentCard } from "@/components/president/PresidentClient";
import { JusticeCard } from "@/components/justice/JusticeClient";
import type { PoliticianProfile, GovernmentDoc } from "@/types/politicians";
import type { Senator } from "@/types/senator";
import type { President } from "@/types/president";
import type { Justice } from "@/types/justice";

const DOC_TYPE_LABELS: Record<string, string> = {
  "Senate Floor Speech": "FLOOR SPEECH",
  "House Floor Speech": "FLOOR SPEECH",
  "Executive Order": "EXEC ORDER",
  "Proclamation": "PROCLAMATION",
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
    <div className="flex items-start gap-3 py-2 border-b border-matrix-green/10 last:border-0">
      <span className="font-mono text-[9px] text-matrix-green/30 tracking-widest shrink-0 mt-0.5 w-24">
        {doc.date ?? "—"}
      </span>
      <div className="flex-1 min-w-0">
        <span className="font-mono text-[9px] text-neon-cyan/50 tracking-widest mr-2">[{typeLabel}]</span>
        {doc.url ? (
          <a
            href={doc.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-xs text-matrix-green/80 hover:text-matrix-green transition-colors"
          >
            {doc.title}
          </a>
        ) : (
          <span className="font-mono text-xs text-matrix-green/60">{doc.title}</span>
        )}
      </div>
    </div>
  );
}

function SectionBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-6">
      <TerminalTitlebar title={title} />
      <div className="border border-t-0 border-matrix-green/20 bg-crt-black/40 p-4">
        {children}
      </div>
    </div>
  );
}

export default function PoliticianProfileClient({ profile }: { profile: PoliticianProfile }) {
  const { identity, branch, hasScorecard, activeIssues, governmentRecord, scorecard } = profile;

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-4xl mx-auto">

          {/* Breadcrumb */}
          <div className="mb-6 font-mono text-[10px] text-matrix-green/30">
            <Link href="/politicians" className="hover:text-matrix-green/60 transition-colors">
              ← POLITICIANS
            </Link>
            <span className="mx-2">/</span>
            <span className="text-matrix-green/50">{branchLabel(branch)}</span>
          </div>

          {/* Vacancy banner */}
          {identity.isCurrent === false && (
            <div className="mb-6 border border-neon-pink/40 bg-neon-pink/5 px-4 py-3">
              <p className="font-mono text-xs text-neon-pink tracking-widest uppercase mb-1">
                Seat Vacant
              </p>
              <p className="font-mono text-[11px] text-matrix-green/60">
                {identity.name} is no longer serving
                {identity.vacancyReason ? ` (${identity.vacancyReason})` : ""}
                {identity.leftOfficeDate ? ` as of ${identity.leftOfficeDate}` : ""}.
                The scores and data below reflect their record while in office.
              </p>
            </div>
          )}

          {/* Active Issues */}
          {activeIssues.length > 0 && (
            <SectionBlock title="in-the-action-center.dat">
              <p className="font-mono text-[9px] text-matrix-green/30 tracking-widest mb-3">
                CURRENTLY ACTIVE IN {activeIssues.length} ISSUE{activeIssues.length !== 1 ? "S" : ""}
              </p>
              <div className="space-y-3">
                {activeIssues.map(issue => (
                  <div key={issue.id} className="border border-matrix-green/15 bg-crt-black/40 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-mono text-[9px] text-matrix-green/30">RANK #{issue.rank}</span>
                          <span className="font-mono text-[9px] text-matrix-green/30">{issue.date}</span>
                        </div>
                        <p className="font-mono text-sm text-matrix-green/90 mb-1">{issue.title}</p>
                        {issue.summary && (
                          <p className="font-mono text-[10px] text-matrix-green/50 line-clamp-2">{issue.summary}</p>
                        )}
                      </div>
                      <Link
                        href={`/issue/${issue.id}`}
                        className="shrink-0 font-mono text-[9px] text-neon-cyan/60 hover:text-neon-cyan transition-colors tracking-widest whitespace-nowrap"
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
            <SectionBlock title="government-record.dat">
              <p className="font-mono text-[9px] text-matrix-green/30 tracking-widest mb-3">
                {governmentRecord.totalDocs} DOCUMENT{governmentRecord.totalDocs !== 1 ? "S" : ""} ON PUBLIC RECORD · VERBATIM SOURCE LINKS
              </p>
              {governmentRecord.recentDocs.map(doc => (
                <DocRow key={doc.id} doc={doc} />
              ))}
              {governmentRecord.totalDocs > 5 && (
                <Link
                  href={`/explore?politician_id=${profile.id}`}
                  className="block mt-3 font-mono text-[10px] text-matrix-green/40 hover:text-matrix-green/70 transition-colors tracking-widest"
                >
                  VIEW ALL {governmentRecord.totalDocs} DOCUMENTS →
                </Link>
              )}
            </SectionBlock>
          )}

          {/* Committee Memberships */}
          {(branch === "senate" || branch === "house") && identity.committees && identity.committees.length > 0 && (
            <SectionBlock title="committee-assignments.dat">
              <div className="space-y-1.5">
                {identity.committees.map((c, i) => (
                  <div key={i} className="flex items-center justify-between gap-3 py-1 border-b border-matrix-green/10 last:border-0">
                    <span className="font-mono text-xs text-matrix-green/70">{c.committeeName}</span>
                    {c.title && (
                      <span className="font-mono text-[9px] tracking-widest border border-neon-cyan/30 text-neon-cyan/60 px-1.5 py-0.5 shrink-0">
                        {c.title.toUpperCase()}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </SectionBlock>
          )}

          {/* Scorecard */}
          {hasScorecard && scorecard && (
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
                />
              )}
              {branch === "president" && (
                <PresidentCard president={scorecard as unknown as President} />
              )}
              {branch === "scotus" && (
                <JusticeCard justice={scorecard as unknown as Justice} />
              )}

              {(branch === "senate" || branch === "house") && identity.state && (
                <div className="mt-3 text-center">
                  <Link
                    href={`/politicians?branch=${branch}&state=${identity.state}`}
                    className="font-mono text-[10px] text-matrix-green/35 hover:text-matrix-green/60 transition-colors tracking-widest"
                  >
                    COMPARE ALL {identity.stateName ?? identity.state} {branch === "senate" ? "SENATORS" : "REPRESENTATIVES"} →
                  </Link>
                </div>
              )}
            </div>
          )}

          {!hasScorecard && (
            <SectionBlock title="scorecard.dat">
              {/* No card header will render below to carry identity, so
                  show a minimal one here — otherwise a not-yet-scored
                  official's page has no name/photo/party anywhere on it. */}
              <div className="flex items-center gap-3 mb-4">
                {identity.thumbnailUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element -- external, varied politician-photo hosts
                  <img
                    src={identity.thumbnailUrl}
                    alt={identity.name}
                    className="w-12 h-12 rounded object-cover border border-matrix-green/20 shrink-0"
                  />
                ) : null}
                <div>
                  <p className="font-pixel text-base text-matrix-green">{identity.name}</p>
                  <p className="font-mono text-[10px] text-matrix-green/40 tracking-widest">
                    {identity.role}
                    {identity.state ? ` · ${identity.stateName ?? identity.state}` : ""}
                  </p>
                </div>
              </div>
              <p className="font-mono text-xs text-matrix-green/30 tracking-widest text-center py-4">
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
