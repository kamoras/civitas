import Link from "next/link";
import type { BallotCandidate } from "@/types/election";
import { DEM_AFFILIATE_PARTIES, candidateName, incumbencyLabel } from "@/lib/elections";
import { cashOnHandDisplay, formatCurrency } from "@/lib/formatting";
import { getScoreColor } from "@/lib/representation";

// FEC's party codes ("DEM"/"REP"/"IND"/...) don't match lib/partyStyles.ts's
// D/R/I keys (those back President/Justice's own party codes), so this
// mirrors PresidentScorecard.tsx's local PARTY map and its fallback
// pattern rather than reusing partyStyles.ts directly.
export const PARTY_META: Record<string, { label: string; color: string; rule: string }> = {
  DEM: { label: "DEMOCRAT", color: "text-dem-blue", rule: "bg-dem-blue" },
  REP: { label: "REPUBLICAN", color: "text-signal-red", rule: "bg-signal-red" },
  IND: { label: "INDEPENDENT", color: "text-ind-purple", rule: "bg-ind-purple" },
  // State affiliates of the Democratic Party, labelled with their own name
  // ("DEMOCRAT (DFL)") from lib/elections.ts's DEM_AFFILIATE_PARTIES. That
  // list is labels only: which codes are the same party is the backend's
  // (FEC_PARTY_ALIASES), sent as each candidate's `partyGroup` — which
  // getPartyMeta falls back to for any code without a label here.
  ...Object.fromEntries(
    Object.entries(DEM_AFFILIATE_PARTIES).map(([code, suffix]) => [
      code,
      { label: `DEMOCRAT (${suffix})`, color: "text-dem-blue", rule: "bg-dem-blue" },
    ])
  ),
  LIB: { label: "LIBERTARIAN", color: "text-ink-lo", rule: "bg-ink-min" },
  GRE: { label: "GREEN", color: "text-phos-mid", rule: "bg-phos-mid" },
  CON: { label: "CONSTITUTION", color: "text-ink-lo", rule: "bg-ink-min" },
  NON: { label: "NO PARTY AFFILIATION", color: "text-ink-lo", rule: "bg-ink-min" },
  NPA: { label: "NO PARTY AFFILIATION", color: "text-ink-lo", rule: "bg-ink-min" },
  NNE: { label: "NO PARTY AFFILIATION", color: "text-ink-lo", rule: "bg-ink-min" },
  UN: { label: "UNAFFILIATED", color: "text-ink-lo", rule: "bg-ink-min" },
  NOP: { label: "NO PARTY PREFERENCE", color: "text-ink-lo", rule: "bg-ink-min" },
  UNK: { label: "UNAFFILIATED/UNKNOWN", color: "text-ink-lo", rule: "bg-ink-min" },
  // FEC's code for a declared write-in: on the state's list, not printed on the ballot.
  W: { label: "WRITE-IN", color: "text-ink-lo", rule: "bg-ink-min" },
};

/** A candidate's party label and colours: their own FEC code's where
 * there is one, else their party group's (a U.S. Taxpayers filer reads as
 * CONSTITUTION), else the bare code. */
export function getPartyMeta(c: { party: string; partyGroup?: string | null }) {
  return (
    PARTY_META[c.party] ??
    PARTY_META[c.partyGroup ?? ""] ?? { label: c.party, color: "text-ink-lo", rule: "bg-ink-min" }
  );
}

/** `showUnconfirmed` is off by default because in a "filers"/"primary"
 * race NOBODY is confirmed — the race-level note already says so, and a
 * badge on every card would be noise. It's switched on only for a race
 * whose list is otherwise state-verified, where an unconfirmed entry is
 * the exception worth marking.
 *
 * `redrawnSeat`: the race is a House seat on new district lines, where the
 * FEC's incumbency code is worded by incumbencyLabel — differently from
 * election day on (`resultsMode`). */
export default function CandidateCard({
  candidate,
  showUnconfirmed = false,
  redrawnSeat = false,
  resultsMode = false,
}: {
  candidate: BallotCandidate;
  showUnconfirmed?: boolean;
  redrawnSeat?: boolean;
  resultsMode?: boolean;
}) {
  const pm = getPartyMeta(candidate);
  const incumbency = incumbencyLabel(
    candidate.incumbentChallenge,
    redrawnSeat,
    candidate.incumbentRecord?.seat,
    resultsMode
  );
  const cash = cashOnHandDisplay(candidate.cashOnHand);
  // UTC date only, sliced from the ISO string — deterministic across
  // server and client renders, so no locale/hydration hazard.
  const syncedOn = candidate.lastFinancialsSync?.slice(0, 10) ?? null;

  return (
    // Party reads as a 3px rule down the left edge rather than a tinted
    // outline around the whole card: it identifies the candidate without
    // wrapping every figure in a partisan colour.
    <article className="relative border border-white/[0.09] bg-surface p-4 pl-5">
      <span className={`absolute inset-y-0 left-0 w-[3px] ${pm.rule}`} aria-hidden="true" />

      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-display text-lg font-semibold leading-tight text-ink-hi">
            {candidate.fecFiled === false ? (
              candidateName(candidate)
            ) : (
              <a
                href={`https://www.fec.gov/data/candidate/${encodeURIComponent(candidate.id)}/`}
                target="_blank"
                rel="noopener noreferrer"
                className="transition-colors hover:text-phos"
              >
                {candidateName(candidate)}{" "}
                <span aria-hidden="true" className="font-mono text-xs text-phos-mid">
                  ↗
                </span>
              </a>
            )}
          </h3>
          <p className={`mt-0.5 font-mono text-xs tracking-[0.1em] ${pm.color}`}>{pm.label}</p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {showUnconfirmed && !candidate.confirmed && (
            <span
              className="border border-dashed border-white/20 px-2 py-0.5 font-mono text-xs tracking-[0.1em] text-ink-min"
              title="This state's primary file doesn't list this candidate — an uncontested primary isn't held, so there is no result to read. Their FEC filing is the source."
            >
              UNCONFIRMED
            </span>
          )}
          {incumbency && (
            <span className="whitespace-nowrap border border-white/15 px-2 py-0.5 font-mono text-xs tracking-[0.1em] text-ink-lo">
              {incumbency}
            </span>
          )}
          {candidate.incumbentRecord && (
            <Link
              href={`/politicians/${candidate.incumbentRecord.id}`}
              className={`font-mono text-xs hover:underline ${getScoreColor(candidate.incumbentRecord.score)}`}
              title="View this member's full Representation Scorecard"
            >
              SCORE: {candidate.incumbentRecord.score.toFixed(0)} →
            </Link>
          )}
        </div>
      </div>

      {candidate.fecFiled === false ? (
        <p className="mt-3 font-mono text-xs tracking-[0.12em] text-ink-min">
          ON THE STATE&apos;S BALLOT · NO FEC FILING
        </p>
      ) : syncedOn == null ? (
        // Never synced ≠ raised $0 — don't show figures that read as zeros.
        <p className="mt-3 font-mono text-xs tracking-[0.12em] text-ink-min">AWAITING FEC SYNC</p>
      ) : (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-1">
            <div>
              <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">Raised</dt>
              <dd className="font-mono text-xl tabular-nums text-ink-hi">
                {candidate.contributions != null ? formatCurrency(candidate.contributions) : "—"}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                {cash?.label ?? "Cash on hand"}
              </dt>
              <dd className="font-mono text-xl tabular-nums text-ink-hi">{cash?.amount ?? "—"}</dd>
            </div>
          </dl>
          <p className="mt-2 font-mono text-xs tracking-[0.08em] text-ink-min">AS OF {syncedOn}</p>
        </>
      )}
    </article>
  );
}
