"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import TerminalTitlebar from "@/components/TerminalTitlebar";
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
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <Link
            href="/bills"
            className="inline-block mb-6 font-mono text-xs text-ink-lo hover:text-phos transition-colors"
          >
            ← BACK TO BILLS
          </Link>

          <div className="panel mb-6">
            <TerminalTitlebar title={bill.billId.toLowerCase()} />
            <div className="p-6">
              <div className="flex flex-wrap items-center gap-2 mb-3">
                <span
                  className="font-mono text-xs uppercase tracking-widest px-2 py-0.5 border"
                  style={{
                    color: stageColor,
                    borderColor: `${stageColor}4d`,
                    backgroundColor: `${stageColor}1a`,
                  }}
                >
                  {stageInfo?.name ?? bill.stage}
                </span>
                {bill.isLaw && (
                  <span className="font-mono text-xs uppercase tracking-widest px-2 py-0.5 border text-signal-cyan border-signal-cyan/40 bg-signal-cyan/10">
                    Became Law
                  </span>
                )}
                {bill.mentionCount > 0 && (
                  <span
                    className="font-mono text-xs px-2 py-0.5 border text-signal-cyan border-white/15 bg-signal-cyan/10"
                    title={`Referenced in ${bill.mentionCount} current Action Center issue${bill.mentionCount === 1 ? "" : "s"}`}
                  >
                    ACTIVE ×{bill.mentionCount}
                  </span>
                )}
              </div>

              <h1 className="mb-2 font-mono text-2xl uppercase tracking-[0.04em] text-ink-hi sm:text-3xl">
                {bill.billId}
              </h1>
              <p className="text-base sm:text-base text-ink leading-relaxed mb-4">{bill.title}</p>

              <Link
                href={`/politicians/${bill.sponsorId}`}
                className="flex items-center gap-2 w-fit hover:text-phos transition-colors mb-4"
              >
                {bill.sponsorThumbnailUrl && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={bill.sponsorThumbnailUrl}
                    alt=""
                    className="w-8 h-8 rounded-full object-cover border border-white/[0.07]"
                  />
                )}
                <span className={`px-1.5 py-0.5 border text-xs font-mono ${party.className}`}>
                  {party.label}
                </span>
                <span className="text-sm">{bill.sponsorName}</span>
                <span className="text-ink-min text-xs">
                  · {bill.sponsorState} · {bill.chamber === "senate" ? "Senate" : "House"}
                </span>
              </Link>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono border-t border-white/[0.07] pt-4">
                <div>
                  <div className="text-ink-min uppercase tracking-widest text-xs mb-1">
                    Introduced
                  </div>
                  <div className="text-ink">{formatDate(bill.introducedDate)}</div>
                </div>
                <div>
                  <div className="text-ink-min uppercase tracking-widest text-xs mb-1">
                    Congress
                  </div>
                  <div className="text-ink">{bill.congress || "—"}</div>
                </div>
                <div className="col-span-2 sm:col-span-2">
                  <div className="text-ink-min uppercase tracking-widest text-xs mb-1">
                    Latest Action
                  </div>
                  <div className="text-ink">
                    {bill.latestAction || "—"}
                    {bill.latestActionDate && (
                      <span className="text-ink-min"> ({formatDate(bill.latestActionDate)})</span>
                    )}
                  </div>
                </div>
              </div>

              {externalUrl && (
                <a
                  href={externalUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block mt-4 font-mono text-xs text-signal-cyan hover:text-phos transition-colors"
                >
                  VIEW ON CONGRESS.GOV ↗
                </a>
              )}
            </div>
          </div>

          {bill.policyAreas.length > 0 && (
            <div className="panel mb-6">
              <TerminalTitlebar title="Policy areas" />
              <div className="p-6 space-y-3">
                {bill.policyAreas.map((area) => (
                  <div key={area.area} className="flex items-center gap-3">
                    <span
                      className="font-mono text-xs text-ink w-40 shrink-0 truncate"
                      title={area.area}
                    >
                      {area.area}
                    </span>
                    <div className="flex-1 h-1.5 bg-white/[0.03] overflow-hidden">
                      <div
                        className="h-full"
                        style={{
                          width: `${Math.round(area.confidence * 100)}%`,
                          backgroundColor: stageColor,
                        }}
                      />
                    </div>
                    <span className="font-mono text-xs text-ink-min w-10 text-right">
                      {Math.round(area.confidence * 100)}%
                    </span>
                  </div>
                ))}
                {bill.partyLeaning && (
                  <p className="font-mono text-xs text-ink-min pt-2 border-t border-white/[0.07]">
                    {PARTY_LEANING_LABEL[bill.partyLeaning] ?? bill.partyLeaning}
                  </p>
                )}
              </div>
            </div>
          )}

          {bill.relatedIssues.length > 0 && (
            <div className="panel">
              <TerminalTitlebar title="Mentions" />
              <div className="p-6">
                <ul className="space-y-2">
                  {bill.relatedIssues.map((issue) => (
                    <li key={issue.id}>
                      <Link
                        href={`/action?date=${issue.date}`}
                        className="flex items-baseline gap-3 text-sm hover:text-phos transition-colors"
                      >
                        <span className="font-mono text-xs text-ink-min shrink-0">
                          {issue.date}
                        </span>
                        <span className="text-ink truncate">{issue.title}</span>
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
