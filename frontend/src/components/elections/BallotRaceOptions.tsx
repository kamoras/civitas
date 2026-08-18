import Link from "next/link";
import { isActiveCandidate } from "@/lib/elections";
import { formatCurrency } from "@/lib/formatting";
import { getScoreColor } from "@/lib/representation";
import type { RaceWithCandidates } from "@/types/election";

// FEC's party codes don't match lib/partyStyles.ts's D/R/I keys — same
// local mapping CandidateCard.tsx already uses, kept small here since
// this view only needs a label/color, not the full fundraising card.
const PARTY_META: Record<string, { label: string; color: string }> = {
  DEM: { label: "Democrat", color: "text-dem-blue" },
  REP: { label: "Republican", color: "text-signal-red" },
  IND: { label: "Independent", color: "text-ink" },
  DFL: { label: "Democrat (DFL)", color: "text-dem-blue" },
  DNL: { label: "Democrat (D-NPL)", color: "text-dem-blue" },
  LIB: { label: "Libertarian", color: "text-ink" },
  GRE: { label: "Green", color: "text-ink" },
  CON: { label: "Constitution", color: "text-ink" },
  NON: { label: "No party affiliation", color: "text-ink-lo" },
  NPA: { label: "No party affiliation", color: "text-ink-lo" },
  NNE: { label: "No party affiliation", color: "text-ink-lo" },
  UNK: { label: "Unaffiliated/unknown", color: "text-ink-lo" },
};

function getPartyMeta(party: string) {
  return PARTY_META[party] ?? { label: party, color: "text-ink-lo" };
}

/** One race's candidates, presented as ballot choices — a marker, name,
 * party, and cash-on-hand, not the full fundraising-focused
 * CandidateCard (raised total, FEC profile link, etc.). This page
 * answers "what are my options", including the headline money figure,
 * inline rather than behind a click (2026-08 review); the deeper
 * financial history and per-race coverage archive stay one link away
 * on race-detail. */
export default function BallotRaceOptions({ race }: { race: RaceWithCandidates }) {
  const active = race.candidates.filter(isActiveCandidate);
  const otherCount = race.candidates.length - active.length;

  return (
    <div>
      {active.length === 0 ? (
        <p className="text-xs text-ink-lo">No candidates on record for this race yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {active.map((c) => {
            const pm = getPartyMeta(c.party);
            return (
              <li
                key={c.id}
                className="flex items-center gap-3 border border-white/[0.07] bg-surface px-3 py-2 flex-wrap sm:flex-nowrap"
              >
                <span aria-hidden="true" className="text-ink-min text-sm shrink-0">
                  ○
                </span>
                {/* Name gets its own line's worth of room before badges
                    wrap — on a narrow screen, party + incumbent + score
                    badges together left almost no width for the name,
                    which truncated to nearly nothing (verified on a
                    real 390px viewport). basis-full forces badges onto
                    their own row below the name at that width; from sm
                    up there's room for everything on one line. */}
                <span className="text-sm text-ink-hi flex-1 min-w-0 basis-full sm:basis-auto truncate">
                  {c.name}
                </span>
                <span className={`text-xs font-mono shrink-0 ${pm.color}`}>
                  {pm.label.toUpperCase()}
                </span>
                {/* Null lastFinancialsSync means "never synced", not
                    "raised $0" — omit the figure entirely rather than
                    show a number that reads as zero (same discipline
                    CandidateCard's "AWAITING FEC SYNC" state applies). */}
                {c.lastFinancialsSync && c.cashOnHand != null && (
                  <span
                    className="text-xs font-mono shrink-0 text-ink tabular-nums"
                    title={
                      c.cashOnHand < 0
                        ? `As of ${c.lastFinancialsSync.slice(0, 10)}: the campaign's debts exceed its cash on hand (real FEC data, not an error)`
                        : `Cash on hand as of ${c.lastFinancialsSync.slice(0, 10)}`
                    }
                  >
                    {formatCurrency(c.cashOnHand)} CASH
                  </span>
                )}
                {c.incumbentChallenge === "I" && (
                  <span className="text-xs font-mono px-1.5 py-0.5 border border-white/[0.07] text-ink-lo shrink-0">
                    INCUMBENT
                  </span>
                )}
                {c.incumbentRecord && (
                  <Link
                    href={`/politicians/${c.incumbentRecord.id}`}
                    className={`text-xs font-mono shrink-0 hover:underline ${getScoreColor(c.incumbentRecord.score)}`}
                    title="View this member's full Representation Scorecard"
                  >
                    SCORE: {c.incumbentRecord.score.toFixed(0)} →
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {otherCount > 0 && (
        <p className="text-xs text-ink-min mt-2">
          + {otherCount} other FEC {otherCount === 1 ? "filer" : "filers"} (paper filings or
          prior-cycle records, not shown as ballot options).
        </p>
      )}
      <Link
        href={`/elections/${race.id}`}
        className="inline-block mt-3 text-xs text-signal-cyan hover:text-phos transition-colors"
      >
        Full race detail &amp; fundraising history →
      </Link>
    </div>
  );
}
