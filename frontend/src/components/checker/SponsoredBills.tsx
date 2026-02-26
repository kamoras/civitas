"use client";

import { useState } from "react";
import { SponsoredBill } from "@/types/senator";
import { billUrl } from "@/lib/sources";
import CollapsibleSection from "./CollapsibleSection";
import MetricTooltip from "./MetricTooltip";

interface SponsoredBillsProps {
  bills: SponsoredBill[];
}

const PARTY_BADGE: Record<string, { label: string; className: string }> = {
  R: { label: "R", className: "text-rep-red border-rep-red/30 bg-rep-red/10" },
  D: { label: "D", className: "text-dem-blue border-dem-blue/30 bg-dem-blue/10" },
  bipartisan: { label: "BP", className: "text-ind-purple border-ind-purple/30 bg-ind-purple/10" },
};

const INITIAL_VISIBLE = 8;

export default function SponsoredBills({ bills }: SponsoredBillsProps) {
  const [showAll, setShowAll] = useState(false);

  if (!bills || bills.length === 0) return null;

  const lawCount = bills.filter((b) => b.isLaw).length;
  const visible = showAll ? bills : bills.slice(0, INITIAL_VISIBLE);

  const summaryParts: string[] = [`${bills.length} bills`];
  if (lawCount > 0) summaryParts.push(`${lawCount} became law`);

  return (
    <CollapsibleSection
      title="SPONSORED LEGISLATION"
      summary={summaryParts.join(" · ")}
      source="congress.gov"
    >
      <div className="space-y-3">
        {/* Quick stats */}
        <div className="grid grid-cols-3 gap-2 text-center text-sm">
          <div className="terminal-window p-2">
            <div className="text-xl font-pixel text-matrix-green">{bills.length}</div>
            <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Number of bills this senator introduced as primary sponsor. Sponsoring a bill means they authored or championed it.">BILLS SPONSORED</MetricTooltip></div>
          </div>
          <div className="terminal-window p-2">
            <div className={`text-xl font-pixel ${lawCount > 0 ? "text-neon-cyan" : "text-matrix-green/30"}`}>
              {lawCount}
            </div>
            <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="How many of this senator's sponsored bills were signed into law. Most bills never pass — even 1 is notable.">BECAME LAW</MetricTooltip></div>
          </div>
          <div className="terminal-window p-2">
            <div className="text-xl font-pixel text-neon-yellow">
              {lawCount > 0 ? `${Math.round((lawCount / bills.length) * 100)}%` : "0%"}
            </div>
            <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Percentage of sponsored bills that became law. The average senator has a ~3-5% success rate, so even low numbers can be typical.">SUCCESS RATE</MetricTooltip></div>
          </div>
        </div>

        {/* Bill list */}
        <div className="space-y-1.5">
          {visible.map((bill) => {
            const url = billUrl(bill.billId);
            const badge = bill.partyLeaning ? PARTY_BADGE[bill.partyLeaning] : null;
            return (
              <div
                key={bill.billId}
                className={`terminal-window p-2.5 border-l-4 ${
                  bill.isLaw
                    ? "border-l-neon-cyan/50"
                    : "border-l-matrix-green/20"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      {url ? (
                        <a
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm text-matrix-green/80 hover:text-neon-cyan transition-colors"
                        >
                          {bill.title}
                        </a>
                      ) : (
                        <span className="text-sm text-matrix-green/80">{bill.title}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <span className="text-[10px] text-matrix-green/30">{bill.billId}</span>
                      {bill.introducedDate && (
                        <span className="text-[10px] text-matrix-green/30">{bill.introducedDate}</span>
                      )}
                      {bill.isLaw && (
                        <span className="text-[10px] px-1.5 py-0.5 border text-neon-cyan border-neon-cyan/30 bg-neon-cyan/10 font-pixel">
                          SIGNED INTO LAW
                        </span>
                      )}
                      {badge && (
                        <span className={`text-[10px] px-1 py-0.5 border font-pixel ${badge.className}`}>
                          {badge.label}
                        </span>
                      )}
                      {bill.policyAreas?.filter(a => a.area !== "PROCEDURAL").map((a) => (
                        <span
                          key={a.area}
                          className={`text-[10px] px-1.5 py-0.5 border ${
                            a.party === "R"
                              ? "text-red-400/70 border-red-400/30 bg-red-400/5"
                              : a.party === "D"
                              ? "text-blue-400/70 border-blue-400/30 bg-blue-400/5"
                              : "text-neon-yellow/70 border-neon-yellow/30 bg-neon-yellow/5"
                          }`}
                        >
                          {a.area}
                        </span>
                      ))}
                    </div>
                    {bill.latestAction && (
                      <div className="text-[10px] text-matrix-green/40 mt-1 truncate">
                        {bill.latestAction}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {bills.length > INITIAL_VISIBLE && (
          <button
            onClick={() => setShowAll(!showAll)}
            className="text-xs text-matrix-green/50 hover:text-matrix-green transition-colors font-terminal"
          >
            {showAll ? `[-] Show less` : `[+] Show all ${bills.length} bills`}
          </button>
        )}
      </div>
    </CollapsibleSection>
  );
}
