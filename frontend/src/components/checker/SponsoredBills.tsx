"use client";

import { useState } from "react";
import Link from "next/link";
import { SponsoredBill } from "@/types/senator";
import CollapsibleSection from "../shared/CollapsibleSection";
import MetricTooltip from "./MetricTooltip";
import { PARTY_BADGE, policyAreaBadgeClass } from "@/lib/partyStyles";

interface SponsoredBillsProps {
  bills: SponsoredBill[];
}

const INITIAL_VISIBLE = 8;

// Matches SUBSTANTIVE_BILL_TYPES in backend/app/pipeline/analyze/score_calculator.py —
// simple/concurrent resolutions (sres/hres/sconres/hconres) are routinely
// ceremonial ("designating April as Second Chance Month") and "agreed to"
// without debate by unanimous consent. Legislative Effectiveness weights
// them 1x against a bill's 5x (Volden & Wiseman's commemorative tier), so
// counting them here as "became law" or "advancing" would put a bigger
// number next to a score that credited them a fifth as much.
const SUBSTANTIVE_BILL_TYPES = new Set(["s", "hr", "sjres", "hjres"]);

// Deliberately excludes IN_COMMITTEE, unlike MAIN_FLOW_STAGES in
// BillStageFlow.tsx (the site-wide /bills funnel, which correctly shows
// it as a real, factual current status). 2026-07 fix: backend/app/
// pipeline/analyze/bill_stage.py assigns IN_COMMITTEE the moment a bill
// is automatically referred to committee — the default first step for
// essentially every bill, not a sign anyone did anything — and can't yet
// distinguish that from a bill that actually got a hearing or markup
// (both collapse into the same stage). Counting it as "advancing" made
// "past the starting line" describe the starting line itself: audited
// live, one senator's sponsored-bills summary read "135 bills, 123
// advancing" — 91% of her substantive bills, because nearly all of them
// simply hadn't died yet, not because they were unusually far along.
// ON_FLOOR (2026-09) is past committee by definition: reported out and
// taken up by the chamber, which is exactly what "advancing" means here.
const ADVANCING_STAGES = new Set(["ON_FLOOR", "PASSED_CHAMBER", "IN_OTHER_CHAMBER", "TO_PRESIDENT"]);

// `stage` (BILL_STAGES taxonomy, backend/app/config_definitions.py) is the
// more reliable signal when present. Falling back to the original
// latestAction string-match covers any sponsored bill from before the
// `stage` backfill (or a future edge case where classification fails) —
// once `stage` is populated for a bill this prefers it outright.
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

  const substantive = bills.filter((b) =>
    SUBSTANTIVE_BILL_TYPES.has((b.billType || "").toLowerCase())
  );
  const lawBills = substantive.filter((b) => b.isLaw);
  const advancingBills = substantive.filter(isAdvancing);
  const lawCount = lawBills.length;
  const advancedCount = advancingBills.length;

  const filtered = filter === "law" ? lawBills : filter === "advancing" ? advancingBills : bills;
  const visible = showAll ? filtered : filtered.slice(0, INITIAL_VISIBLE);

  const toggleFilter = (next: BillFilter) =>
    setFilter((current) => (current === next ? "all" : next));

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
          <FilterTile
            count={bills.length}
            label="BILLS SPONSORED"
            help="Number of bills and resolutions this member introduced as primary sponsor this congress. Sponsoring a bill means they authored or championed it."
            pressed={filter === "all"}
            onSelect={() => setFilter("all")}
            activeClass="border-ink-lo bg-white/[0.06]"
            countClass="text-ink-hi"
          />
          <FilterTile
            count={lawCount}
            label="BECAME LAW"
            help="How many of this member's sponsored bills (S./H.R./joint resolutions — not simple/concurrent resolutions) were signed into law. Most bills never pass — even 1 is notable. Click to filter the list below to just these."
            pressed={filter === "law"}
            onSelect={() => toggleFilter("law")}
            activeClass="border-signal-cyan/40 bg-signal-cyan/10"
            countClass="text-signal-cyan"
          />
          <FilterTile
            count={advancedCount}
            label="ADVANCING"
            help="Bills (S./H.R./joint resolutions) that have passed at least one chamber and haven't yet become law. Being referred to committee doesn't count — nearly every bill is, automatically. Simple/concurrent resolutions (e.g. designating an awareness month) are left out: they're routinely agreed to without debate, and Legislative Effectiveness weights them a fifth as much as a bill. Click to filter the list below to just these."
            pressed={filter === "advancing"}
            onSelect={() => toggleFilter("advancing")}
            activeClass="border-signal-amber/40 bg-signal-amber/10"
            countClass="text-signal-amber"
          />
        </div>

        {filter !== "all" && (
          <button
            onClick={() => setFilter("all")}
            className="font-mono text-xs text-ink-min hover:text-phos transition-colors tracking-widest"
          >
            CLEAR FILTER
          </button>
        )}

        {/* Bill list */}
        <div className="space-y-1.5">
          {visible.map((bill) => {
            const url = `/bills/${encodeURIComponent(bill.billId)}`;
            const badge = bill.partyLeaning ? PARTY_BADGE[bill.partyLeaning] : null;
            return (
              <div
                key={bill.billId}
                className={`panel p-2.5 border-l-4 ${
                  bill.isLaw ? "border-l-signal-cyan" : "border-l-white/[0.07]"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Link
                        href={url}
                        className="text-sm text-ink hover:text-phos transition-colors"
                      >
                        {bill.title}
                      </Link>
                    </div>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <span className="text-xs text-ink-min">{bill.billId}</span>
                      {bill.introducedDate && (
                        <span className="text-xs text-ink-min">{bill.introducedDate}</span>
                      )}
                      {bill.isLaw && (
                        <span className="text-xs px-1.5 py-0.5 border text-signal-cyan border-white/15 bg-signal-cyan/10 font-mono">
                          SIGNED INTO LAW
                        </span>
                      )}
                      {bill.commemorative && (
                        <span className="text-xs px-1.5 py-0.5 border border-white/15 text-ink-lo font-mono">
                          <MetricTooltip text="Reads as commemorative — naming a post office or building, awarding a medal, recognising a person or event. Legislative Effectiveness weights it 1× instead of a bill's 5×, as Volden & Wiseman's scores do. Detected from the title by a classifier calibrated against their coding.">
                            COMMEMORATIVE · 1×
                          </MetricTooltip>
                        </span>
                      )}
                      {badge && (
                        <span className={`text-xs px-1 py-0.5 border font-mono ${badge.className}`}>
                          {badge.label}
                        </span>
                      )}
                      {bill.policyAreas
                        ?.filter((a) => a.area !== "PROCEDURAL")
                        .map((a) => (
                          <span
                            key={a.area}
                            className={`text-xs px-1.5 py-0.5 border ${policyAreaBadgeClass(a.party)}`}
                          >
                            {a.area}
                          </span>
                        ))}
                    </div>
                    {bill.latestAction && (
                      <div className="text-xs text-ink-min mt-1 truncate">{bill.latestAction}</div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {filtered.length === 0 && (
          <div className="text-xs text-ink-min font-mono py-2">No bills match this filter.</div>
        )}

        {filtered.length > INITIAL_VISIBLE && (
          <button
            onClick={() => setShowAll(!showAll)}
            className="text-xs text-ink-lo hover:text-phos transition-colors font-mono"
          >
            {showAll ? `[-] Show less` : `[+] Show all ${filtered.length} bills`}
          </button>
        )}
      </div>
    </CollapsibleSection>
  );
}

interface FilterTileProps {
  count: number;
  label: string;
  help: string;
  pressed: boolean;
  onSelect: () => void;
  activeClass: string;
  countClass: string;
}

/**
 * One filter toggle. The count and label are a real <button>; the [?]
 * explanation sits beside it, not inside. It used to be a div[role=button]
 * wrapping the label's MetricTooltip — which renders its own <button> — and
 * an interactive element inside another is announced as one control, so the
 * explanation was unreachable to a screen reader (axe: nested-interactive).
 * A zero count can't filter to anything, so its tile is disabled (the
 * explanation stays reachable).
 */
function FilterTile({ count, label, help, pressed, onSelect, activeClass, countClass }: FilterTileProps) {
  const empty = count === 0;
  return (
    <div className={`panel p-2 transition-colors ${pressed ? activeClass : empty ? "" : "hover:bg-white/[0.03]"}`}>
      <button
        type="button"
        onClick={onSelect}
        disabled={empty}
        aria-pressed={pressed}
        className={`block w-full ${empty ? "cursor-default" : "cursor-pointer"}`}
      >
        <span className={`block text-xl font-display font-semibold ${empty ? "text-ink-min" : countClass}`}>
          {count}
        </span>{" "}
        <span className="block text-xs text-ink-min">{label}</span>
      </button>
      <span className="text-xs text-ink-min">
        <MetricTooltip text={help} />
      </span>
    </div>
  );
}
