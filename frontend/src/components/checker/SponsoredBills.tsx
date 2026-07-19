"use client";

import { useState } from "react";
import { SponsoredBill } from "@/types/senator";
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

// Matches MAIN_FLOW_STAGES in BillStageFlow.tsx minus INTRODUCED/ENACTED —
// "advancing" means past the starting line and short of the finish, same
// definition the site-wide /bills stage funnel uses.
const ADVANCING_STAGES = new Set(["IN_COMMITTEE", "PASSED_CHAMBER", "IN_OTHER_CHAMBER", "TO_PRESIDENT"]);

// `stage` (BILL_STAGES taxonomy, backend/app/config_definitions.py) is the
// more reliable signal when present, but live data currently has it empty
// for every sponsored bill (the field was added to the pipeline after most
// rows were last written — it backfills on the next full run). Falling
// back to the original latestAction string-match keeps "advancing" working
// today instead of silently reporting 0 for everyone until that backfill
// lands; once `stage` is populated for a bill this prefers it outright.
function isAdvancing(b: SponsoredBill): boolean {
  if (b.isLaw) return false;
  if (b.stage) return ADVANCING_STAGES.has(b.stage);
  const action = (b.latestAction || "").toLowerCase();
  return (
    action.includes("passed") ||
    action.includes("agreed to") ||
    action.includes("ordered to be reported") ||
    action.includes("reported by")
  );
}

type BillFilter = "all" | "law" | "advancing";

export default function SponsoredBills({ bills }: SponsoredBillsProps) {
  const [showAll, setShowAll] = useState(false);
  const [filter, setFilter] = useState<BillFilter>("all");

  if (!bills || bills.length === 0) return null;

  const substantive = bills.filter((b) => SUBSTANTIVE_BILL_TYPES.has((b.billType || "").toLowerCase()));
  const lawBills = substantive.filter((b) => b.isLaw);
  const advancingBills = substantive.filter(isAdvancing);
  const lawCount = lawBills.length;
  const advancedCount = advancingBills.length;

  const filtered = filter === "law" ? lawBills : filter === "advancing" ? advancingBills : bills;
  const visible = showAll ? filtered : filtered.slice(0, INITIAL_VISIBLE);

  const toggleFilter = (next: BillFilter) => setFilter((current) => (current === next ? "all" : next));

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
          {/* div[role=button], not <button> — MetricTooltip renders its own
              [?] <button> internally, and a <button> can't nest a <button>
              (invalid HTML, throws a hydration error). */}
          <div
            role="button"
            tabIndex={0}
            onClick={() => setFilter("all")}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setFilter("all"); } }}
            aria-pressed={filter === "all"}
            className={`terminal-window p-2 transition-colors cursor-pointer ${filter === "all" ? "border-matrix-green/60 bg-matrix-green/5" : "hover:bg-white/[0.03]"}`}
          >
            <div className="text-xl font-pixel text-matrix-green">{bills.length}</div>
            <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Number of bills this senator introduced as primary sponsor. Sponsoring a bill means they authored or championed it.">BILLS SPONSORED</MetricTooltip></div>
          </div>
          <div
            role="button"
            tabIndex={lawCount === 0 ? -1 : 0}
            onClick={() => { if (lawCount > 0) toggleFilter("law"); }}
            onKeyDown={(e) => { if (lawCount > 0 && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggleFilter("law"); } }}
            aria-pressed={filter === "law"}
            aria-disabled={lawCount === 0}
            className={`terminal-window p-2 transition-colors ${lawCount === 0 ? "cursor-default" : "cursor-pointer"} ${filter === "law" ? "border-neon-cyan/60 bg-neon-cyan/5" : lawCount > 0 ? "hover:bg-white/[0.03]" : ""}`}
          >
            <div className={`text-xl font-pixel ${lawCount > 0 ? "text-neon-cyan" : "text-matrix-green/30"}`}>
              {lawCount}
            </div>
            <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="How many of this senator's sponsored bills (S./H.R./joint resolutions — not simple/concurrent resolutions) were signed into law. Most bills never pass — even 1 is notable. Click to filter the list below to just these.">BECAME LAW</MetricTooltip></div>
          </div>
          <div
            role="button"
            tabIndex={advancedCount === 0 ? -1 : 0}
            onClick={() => { if (advancedCount > 0) toggleFilter("advancing"); }}
            onKeyDown={(e) => { if (advancedCount > 0 && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggleFilter("advancing"); } }}
            aria-pressed={filter === "advancing"}
            aria-disabled={advancedCount === 0}
            className={`terminal-window p-2 transition-colors ${advancedCount === 0 ? "cursor-default" : "cursor-pointer"} ${filter === "advancing" ? "border-neon-yellow/60 bg-neon-yellow/5" : advancedCount > 0 ? "hover:bg-white/[0.03]" : ""}`}
          >
            <div className={`text-xl font-pixel ${advancedCount > 0 ? "text-neon-yellow" : "text-matrix-green/30"}`}>
              {advancedCount}
            </div>
            <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Substantive bills (S./H.R./joint resolutions) that passed at least one chamber or were reported out of committee. Simple/concurrent resolutions (commemorative, e.g. designating an awareness month) are excluded — they're routinely agreed to without debate and aren't meaningful legislative progress, matching how the Legislative Effectiveness score itself counts advancement. Click to filter the list below to just these.">ADVANCING</MetricTooltip></div>
          </div>
        </div>

        {filter !== "all" && (
          <button
            onClick={() => setFilter("all")}
            className="font-mono text-[9px] text-matrix-green/30 hover:text-matrix-green/60 transition-colors tracking-widest"
          >
            CLEAR FILTER
          </button>
        )}

        {/* Bill list */}
        <div className="space-y-1.5">
          {visible.map((bill) => {
            const url = `/bills?q=${encodeURIComponent(bill.billId)}`;
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
                      <a
                        href={safeHref(url) || "#"}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm text-matrix-green/80 hover:text-neon-cyan transition-colors"
                      >
                        {bill.title}
                      </a>
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

        {filtered.length === 0 && (
          <div className="text-xs text-matrix-green/40 font-terminal py-2">
            No bills match this filter.
          </div>
        )}

        {filtered.length > INITIAL_VISIBLE && (
          <button
            onClick={() => setShowAll(!showAll)}
            className="text-xs text-matrix-green/50 hover:text-matrix-green transition-colors font-terminal"
          >
            {showAll ? `[-] Show less` : `[+] Show all ${filtered.length} bills`}
          </button>
        )}
      </div>
    </CollapsibleSection>
  );
}
