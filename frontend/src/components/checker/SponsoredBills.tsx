"use client";

import { useState } from "react";
import { SponsoredBill } from "@/types/senator";
import { billUrl } from "@/lib/sources";
import { safeHref } from "@/lib/formatting";
import CollapsibleSection from "../shared/CollapsibleSection";
import MetricTooltip from "./MetricTooltip";
import { PARTY_BADGE } from "@/lib/partyStyles";

interface SponsoredBillsProps {
  bills: SponsoredBill[];
}

const INITIAL_VISIBLE = 8;

// Matches SUBSTANTIVE_BILL_TYPES in backend/app/pipeline/analyze/score_calculator.py —
// simple/concurrent resolutions (sres/hres/sconres/hconres) are routinely
// ceremonial ("designating April as Second Chance Month") and "agreed to"
// without debate by unanimous consent. The Legislative Effectiveness score
// already excludes them from its advancement count for exactly this reason
// (the "Mushroom Day" fix) — this summary count must use the same filter,
// or it displays a bigger, more impressive "advancing" number right next
// to a score that didn't credit any of it, an unexplained contradiction
// on the same card.
const SUBSTANTIVE_BILL_TYPES = new Set(["s", "hr", "sjres", "hjres"]);

export default function SponsoredBills({ bills }: SponsoredBillsProps) {
  const [showAll, setShowAll] = useState(false);

  if (!bills || bills.length === 0) return null;

  const substantive = bills.filter((b) => SUBSTANTIVE_BILL_TYPES.has((b.billType || "").toLowerCase()));
  const lawCount = substantive.filter((b) => b.isLaw).length;
  const advancedCount = substantive.filter((b) => {
    if (b.isLaw) return false;
    const action = (b.latestAction || "").toLowerCase();
    return (
      action.includes("passed") ||
      action.includes("agreed to") ||
      action.includes("ordered to be reported") ||
      action.includes("reported by")
    );
  }).length;
  const visible = showAll ? bills : bills.slice(0, INITIAL_VISIBLE);

  const summaryParts: string[] = [`${bills.length} bills`];
  if (lawCount > 0) summaryParts.push(`${lawCount} became law`);
  if (advancedCount > 0) summaryParts.push(`${advancedCount} advancing`);

  return (
    <CollapsibleSection
      title="SPONSORED LEGISLATION"
      summary={summaryParts.join(" · ")}
      source="congress.gov"
    >
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-2 text-center text-sm">
          <div className="terminal-window p-2">
            <div className="text-xl font-pixel text-matrix-green">{bills.length}</div>
            <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Number of bills this senator introduced as primary sponsor. Sponsoring a bill means they authored or championed it.">BILLS SPONSORED</MetricTooltip></div>
          </div>
          <div className="terminal-window p-2">
            <div className={`text-xl font-pixel ${lawCount > 0 ? "text-neon-cyan" : "text-matrix-green/30"}`}>
              {lawCount}
            </div>
            <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="How many of this senator's sponsored bills (S./H.R./joint resolutions — not simple/concurrent resolutions) were signed into law. Most bills never pass — even 1 is notable.">BECAME LAW</MetricTooltip></div>
          </div>
          <div className="terminal-window p-2">
            <div className={`text-xl font-pixel ${advancedCount > 0 ? "text-neon-yellow" : "text-matrix-green/30"}`}>
              {advancedCount}
            </div>
            <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Substantive bills (S./H.R./joint resolutions) that passed at least one chamber or were reported out of committee. Simple/concurrent resolutions (commemorative, e.g. designating an awareness month) are excluded — they're routinely agreed to without debate and aren't meaningful legislative progress, matching how the Legislative Effectiveness score itself counts advancement.">ADVANCING</MetricTooltip></div>
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
                          href={safeHref(url) || "#"}
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
