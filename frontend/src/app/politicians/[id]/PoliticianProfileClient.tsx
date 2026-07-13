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
import { getScoreLabel, getScoreColor } from "@/lib/corruption";
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

function partyLabel(party: string | undefined) {
  if (party === "D") return { text: "DEMOCRAT", cls: "text-dem-blue border-dem-blue/40 bg-dem-blue/10" };
  if (party === "R") return { text: "REPUBLICAN", cls: "text-rep-red border-rep-red/40 bg-rep-red/10" };
  return { text: "INDEPENDENT", cls: "text-ind-purple border-ind-purple/40 bg-ind-purple/10" };
}

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
  const { identity, branch, hasScorecard, overallScore, activeIssues, governmentRecord, scorecard } = profile;
  const party = partyLabel(identity.party ?? undefined);

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

          {/* Identity header */}
          <div className="mb-6 flex flex-col sm:flex-row items-start gap-4">
            {identity.thumbnailUrl ? (
              <img
                src={identity.thumbnailUrl}
                alt={identity.name}
                className="w-20 h-20 rounded object-cover border border-matrix-green/20 shrink-0"
              />
            ) : (
              <div className="w-20 h-20 rounded border border-matrix-green/20 flex items-center justify-center shrink-0 bg-crt-black/60">
                <span className="font-mono text-lg text-matrix-green/30">
                  {identity.name.split(" ").map((w: string) => w[0]).slice(0, 2).join("")}
                </span>
              </div>
            )}
            <div className="flex-1 min-w-0">
              <h1 className="font-pixel text-xl sm:text-2xl text-matrix-green neon-green mb-1">
                {identity.name}
              </h1>
              <div className="flex flex-wrap items-center gap-2 mb-2">
                <span className={`font-mono text-[9px] tracking-widest border px-2 py-0.5 ${party.cls}`}>
                  {party.text}
                </span>
                <span className="font-mono text-[9px] tracking-widest border border-matrix-green/20 text-matrix-green/40 px-2 py-0.5">
                  {branchLabel(branch)}
                </span>
                {identity.state && (
                  <span className="font-mono text-[9px] tracking-widest text-matrix-green/40">
                    {identity.stateName ?? identity.state}
                    {identity.district != null ? ` · District ${identity.district}` : ""}
                  </span>
                )}
                {identity.isCurrent && (
                  <span className="font-mono text-[9px] tracking-widest border border-matrix-green/30 text-matrix-green/60 px-2 py-0.5 animate-pulse">
                    CURRENT
                  </span>
                )}
              </div>
              <p className="font-mono text-xs text-matrix-green/50 mb-3">{identity.role}</p>

              {/* Contact links */}
              <div className="flex flex-wrap gap-3">
                {identity.websiteUrl && (
                  <a href={identity.websiteUrl} target="_blank" rel="noopener noreferrer"
                     className="font-mono text-[10px] text-matrix-green/40 hover:text-matrix-green/70 transition-colors tracking-widest">
                    OFFICIAL SITE ↗
                  </a>
                )}
                {identity.contactFormUrl && (
                  <a href={identity.contactFormUrl} target="_blank" rel="noopener noreferrer"
                     className="font-mono text-[10px] text-neon-cyan/50 hover:text-neon-cyan transition-colors tracking-widest">
                    CONTACT ↗
                  </a>
                )}
              </div>
            </div>

            {/* Overall score bubble */}
            {hasScorecard && overallScore != null && (
              <div className="shrink-0 text-center border border-matrix-green/20 bg-crt-black/60 px-4 py-3">
                <div className={`font-mono text-2xl font-bold ${getScoreColor(overallScore)}`}>
                  {overallScore.toFixed(0)}
                </div>
                <div className="font-mono text-[8px] text-matrix-green/40 tracking-widest mt-1">
                  {getScoreLabel(overallScore)}
                </div>
              </div>
            )}
          </div>

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

          {/* Scorecard */}
          {hasScorecard && scorecard && (
            <div className="mb-6">
              {branch === "senate" && (
                <SenatorCard senator={scorecard as unknown as Senator} chamber="senate" />
              )}
              {branch === "house" && (
                <SenatorCard senator={scorecard as unknown as Senator} chamber="house" />
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
                    href={`/scorecard?branch=${branch}&state=${identity.state}`}
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
