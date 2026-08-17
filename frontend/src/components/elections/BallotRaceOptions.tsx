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
  REP: { label: "Republican", color: "text-rep-red" },
  IND: { label: "Independent", color: "text-white/70" },
  DFL: { label: "Democrat (DFL)", color: "text-dem-blue" },
  DNL: { label: "Democrat (D-NPL)", color: "text-dem-blue" },
  LIB: { label: "Libertarian", color: "text-white/70" },
  GRE: { label: "Green", color: "text-matrix-green/80" },
  CON: { label: "Constitution", color: "text-white/70" },
  NON: { label: "No party affiliation", color: "text-white/50" },
  NPA: { label: "No party affiliation", color: "text-white/50" },
  NNE: { label: "No party affiliation", color: "text-white/50" },
  UNK: { label: "Unaffiliated/unknown", color: "text-white/50" },
};

function getPartyMeta(party: string) {
  return PARTY_META[party] ?? { label: party, color: "text-white/50" };
}

/** What this race's list actually IS. Three quite different things get
 * shown in the same shape — nominees a state has confirmed, people merely
 * on a primary ballot, and raw FEC filers who may never appear on any
 * ballot — and a reader can't tell them apart without being told. The
 * backend decides which (RaceWithCandidates.candidateSource); this only
 * puts it into words. */
const SOURCE_NOTE: Record<RaceWithCandidates["candidateSource"], string> = {
  confirmed: "This state's official general-election ballot for this race.",
  nominees:
    "Nominees confirmed by this state's primary results. Candidates who reach the general election without running in a primary — Libertarian, Green or independent — aren't covered for this state yet, so this list may be short.",
  primary: "On this state's primary ballot — the nominees aren't decided until the primary.",
  filers: "Everyone who has filed with the FEC for this race. This state's official candidate list isn't covered yet, so some of these may never appear on a ballot.",
};

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
        <p className="text-xs text-matrix-green/50">No candidates on record for this race yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {active.map((c) => {
            const pm = getPartyMeta(c.party);
            return (
              <li
                key={c.id}
                className="flex items-center gap-3 border border-matrix-green/15 bg-terminal-bg/40 px-3 py-2 flex-wrap sm:flex-nowrap"
              >
                <span aria-hidden="true" className="text-matrix-green/40 text-sm shrink-0">
                  ○
                </span>
                {/* Name gets its own line's worth of room before badges
                    wrap — on a narrow screen, party + incumbent + score
                    badges together left almost no width for the name,
                    which truncated to nearly nothing (verified on a
                    real 390px viewport). basis-full forces badges onto
                    their own row below the name at that width; from sm
                    up there's room for everything on one line. */}
                <span className="text-sm text-white/90 flex-1 min-w-0 basis-full sm:basis-auto truncate">
                  {c.name}
                </span>
                <span className={`text-[10px] font-pixel shrink-0 ${pm.color}`}>
                  {pm.label.toUpperCase()}
                </span>
                {/* Null lastFinancialsSync means "never synced", not
                    "raised $0" — omit the figure entirely rather than
                    show a number that reads as zero (same discipline
                    CandidateCard's "AWAITING FEC SYNC" state applies). */}
                {c.lastFinancialsSync && c.cashOnHand != null && (
                  <span
                    className="text-[10px] font-pixel shrink-0 text-white/60 tabular-nums"
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
                  <span className="text-[9px] font-pixel px-1.5 py-0.5 border border-matrix-green/20 text-matrix-green/50 shrink-0">
                    INCUMBENT
                  </span>
                )}
                {c.incumbentRecord && (
                  <Link
                    href={`/politicians/${c.incumbentRecord.id}`}
                    className={`text-[10px] font-pixel shrink-0 hover:underline ${getScoreColor(c.incumbentRecord.score)}`}
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
        <p className="text-[10px] text-matrix-green/40 mt-2">
          + {otherCount} other FEC {otherCount === 1 ? "filer" : "filers"} (paper filings or
          prior-cycle records, not shown as ballot options).
        </p>
      )}
      {active.length > 0 && (
        <p className="text-[10px] text-matrix-green/40 mt-2">{SOURCE_NOTE[race.candidateSource]}</p>
      )}
      <Link
        href={`/elections/${race.id}`}
        className="inline-block mt-3 text-[11px] text-neon-cyan/70 hover:text-neon-cyan transition-colors"
      >
        Full race detail &amp; fundraising history →
      </Link>
    </div>
  );
}
