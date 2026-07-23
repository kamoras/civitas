"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import GlitchText from "@/components/effects/GlitchText";
import { useConfig } from "@/hooks/useConfig";
import { billUrl } from "@/lib/sources";
import { PARTY_BADGE } from "@/lib/partyStyles";
import type { BillDetail } from "@/types/bill";

function formatDate(dateStr: string): string {
  if (!dateStr) return "—";
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

const PARTY_LEANING_LABEL: Record<string, string> = {
  D: "Democratic-leaning",
  R: "Republican-leaning",
  bipartisan: "Bipartisan",
};

export default function BillDetailClient({ bill }: { bill: BillDetail }) {
  const config = useConfig();
  const stageInfo = config?.billStages?.[bill.stage];
  const stageColor = stageInfo?.color ?? "#00ff41";
  const party = PARTY_BADGE[bill.sponsorParty] ?? PARTY_BADGE.I;
  const externalUrl = billUrl(bill.billId, bill.congress);

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <Link
            href="/bills"
            className="inline-block mb-6 font-mono text-xs text-matrix-green/50 hover:text-neon-cyan transition-colors"
          >
            ← BACK TO BILLS
          </Link>

          <div className="terminal-window mb-6">
            <TerminalTitlebar title={`${bill.billId.toLowerCase()}.dat`} />
            <div className="p-6">
              <div className="flex flex-wrap items-center gap-2 mb-3">
                <span
                  className="font-mono text-xs uppercase tracking-widest px-2 py-0.5 border rounded"
                  style={{ color: stageColor, borderColor: `${stageColor}4d`, backgroundColor: `${stageColor}1a` }}
                >
                  {stageInfo?.name ?? bill.stage}
                </span>
                {bill.isLaw && (
                  <span className="font-mono text-xs uppercase tracking-widest px-2 py-0.5 border rounded text-neon-cyan border-neon-cyan/40 bg-neon-cyan/10">
                    Became Law
                  </span>
                )}
                {bill.mentionCount > 0 && (
                  <span
                    className="font-mono text-xs px-2 py-0.5 border rounded text-neon-cyan border-neon-cyan/30 bg-neon-cyan/10"
                    title={`Referenced in ${bill.mentionCount} current Action Center issue${bill.mentionCount === 1 ? "" : "s"}`}
                  >
                    ACTIVE ×{bill.mentionCount}
                  </span>
                )}
              </div>

              <GlitchText
                as="h1"
                text={bill.billId}
                className="font-pixel text-lg sm:text-2xl text-matrix-green neon-green block mb-2"
              />
              <p className="text-sm sm:text-base text-matrix-green/80 leading-relaxed mb-4">
                {bill.title}
              </p>

              <Link
                href={`/politicians/${bill.sponsorId}`}
                className="flex items-center gap-2 w-fit hover:text-neon-cyan transition-colors mb-4"
              >
                {bill.sponsorThumbnailUrl && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={bill.sponsorThumbnailUrl}
                    alt=""
                    className="w-8 h-8 rounded-full object-cover border border-matrix-green/20"
                  />
                )}
                <span className={`px-1.5 py-0.5 rounded border text-[10px] font-mono ${party.className}`}>
                  {party.label}
                </span>
                <span className="text-sm">{bill.sponsorName}</span>
                <span className="text-matrix-green/40 text-xs">
                  · {bill.sponsorState} · {bill.chamber === "senate" ? "Senate" : "House"}
                </span>
              </Link>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono border-t border-matrix-green/10 pt-4">
                <div>
                  <div className="text-matrix-green/40 uppercase tracking-widest text-[10px] mb-1">Introduced</div>
                  <div className="text-matrix-green/80">{formatDate(bill.introducedDate)}</div>
                </div>
                <div>
                  <div className="text-matrix-green/40 uppercase tracking-widest text-[10px] mb-1">Congress</div>
                  <div className="text-matrix-green/80">{bill.congress || "—"}</div>
                </div>
                <div className="col-span-2 sm:col-span-2">
                  <div className="text-matrix-green/40 uppercase tracking-widest text-[10px] mb-1">Latest Action</div>
                  <div className="text-matrix-green/80">
                    {bill.latestAction || "—"}
                    {bill.latestActionDate && (
                      <span className="text-matrix-green/40"> ({formatDate(bill.latestActionDate)})</span>
                    )}
                  </div>
                </div>
              </div>

              {externalUrl && (
                <a
                  href={externalUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block mt-4 font-mono text-xs text-neon-cyan/80 hover:text-neon-cyan transition-colors"
                >
                  VIEW ON CONGRESS.GOV ↗
                </a>
              )}
            </div>
          </div>

          {bill.policyAreas.length > 0 && (
            <div className="terminal-window mb-6">
              <TerminalTitlebar title="policy_areas.txt" />
              <div className="p-6 space-y-3">
                {bill.policyAreas.map((area) => (
                  <div key={area.area} className="flex items-center gap-3">
                    <span className="font-mono text-xs text-matrix-green/70 w-40 shrink-0 truncate" title={area.area}>
                      {area.area}
                    </span>
                    <div className="flex-1 h-1.5 bg-matrix-green/10 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.round(area.confidence * 100)}%`,
                          backgroundColor: stageColor,
                        }}
                      />
                    </div>
                    <span className="font-mono text-[10px] text-matrix-green/40 w-10 text-right">
                      {Math.round(area.confidence * 100)}%
                    </span>
                  </div>
                ))}
                {bill.partyLeaning && (
                  <p className="font-mono text-[11px] text-matrix-green/40 pt-2 border-t border-matrix-green/10">
                    {PARTY_LEANING_LABEL[bill.partyLeaning] ?? bill.partyLeaning}
                  </p>
                )}
              </div>
            </div>
          )}

          {bill.relatedIssues.length > 0 && (
            <div className="terminal-window">
              <TerminalTitlebar title="action_center_mentions.txt" />
              <div className="p-6">
                <ul className="space-y-2">
                  {bill.relatedIssues.map((issue) => (
                    <li key={issue.id}>
                      <Link
                        href={`/action?date=${issue.date}`}
                        className="flex items-baseline gap-3 text-sm hover:text-neon-cyan transition-colors"
                      >
                        <span className="font-mono text-[10px] text-matrix-green/30 shrink-0">{issue.date}</span>
                        <span className="text-matrix-green/80 truncate">{issue.title}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
