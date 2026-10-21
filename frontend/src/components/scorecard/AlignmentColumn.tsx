"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { fetchRepVotes, fetchSenatorVotes } from "@/lib/api";
import { rollCallHref } from "@/lib/congress";
import { voteNominationLine, voteSubjectHref } from "@/lib/nominations";
import type { AlignmentFacts, BreakVote, ScoreBreakdownDimension } from "@/types/scoreBreakdown";
import type { ConstituentApproval as Approval } from "@/types/senator";
import ConstituentApproval from "./ConstituentApproval";
import ScoreColumn, { Block } from "./ScoreColumn";
import { count, partyMembers, percentOneDecimal, shortDate, voteTitle } from "./format";

const PARTY_SHORT: Record<string, string> = { R: "Republicans", D: "Democrats" };
const FLANK: Record<string, string> = { R: "right", D: "left" };
const OTHER_PARTY: Record<string, string> = { R: "Democrats", D: "Republicans" };
// Breaks shown in the column; the drawer lists every vote.
const BREAKS_SHOWN = 6;
const FLANK_SHOWN = 3;

const TOOLTIP =
  "How often the member broke with the party on this Congress's party-line votes, beside how often members of the same party in seats that lean the same way do. Only breaks toward the other party count; votes against the party from its own flank are listed apart. Below, how the member's own constituents rate them, by party.";

/** A break as the list shows it: the breakdown's (BreakVote) or, before the
 *  member's whole-Congress record is measured, a stored vote. */
type ListedBreak = Omit<BreakVote, "rollCall"> & {
  rollCall?: BreakVote["rollCall"];
  billId?: string;
  billName?: string;
  date?: string;
};

function Lede({ facts, seat }: { facts: AlignmentFacts; seat: string }) {
  const members = partyMembers(facts.party);
  if (facts.breakRate == null || facts.breaks == null) {
    return (
      <p className="text-base leading-relaxed text-ink">
        {count(facts.partyVotes, "party-line vote", "party-line votes")} on record so far: too few
        to compare with how often {members} in similar seats break with the party.
      </p>
    );
  }
  return (
    <p className="text-base leading-relaxed text-ink">
      Voted against most {members} on {facts.breaks} of {facts.partyVotes} party-line votes (
      {percentOneDecimal(facts.breakRate)}).
      {facts.expectedBreakRate != null &&
        ` ${members} in ${seat} do that on ${percentOneDecimal(facts.expectedBreakRate)}.`}
    </p>
  );
}

/** The member's break rate beside their seat's norm, on one line. Only the
 *  axis is chosen here (wide enough for both marks); both rates are the
 *  scorer's. */
function RateScale({ rate, expected, name }: { rate: number; expected: number; name: string }) {
  const top = Math.max(0.1, Math.ceil(Math.max(rate, expected) * 12) / 10);
  const at = (r: number) => `${(r / top) * 100}%`;
  return (
    <div
      role="img"
      aria-label={`${name} breaks with the party on ${percentOneDecimal(rate)} of party-line votes; the average for similar seats is ${percentOneDecimal(expected)}`}
    >
      <div className="relative h-8" aria-hidden="true">
        <span className="absolute inset-x-0 top-[15px] h-0.5 bg-white/[0.14]" />
        <span
          className="absolute top-[7px] h-[18px] w-0.5 bg-ink-lo"
          style={{ left: at(expected) }}
        />
        <span
          className="absolute top-[5px] -ml-1.5 h-[22px] w-3 bg-signal-red"
          style={{ left: at(rate) }}
        />
      </div>
      <div className="relative h-4 font-mono text-xs" aria-hidden="true">
        <span className="absolute left-0 text-ink-min">0%</span>
        <span className="absolute right-0 text-ink-min">{Math.round(top * 100)}%</span>
      </div>
      <p className="mt-1 flex flex-wrap gap-x-4 font-mono text-xs" aria-hidden="true">
        <span className="text-ink-lo">
          <span className="mr-1 inline-block h-2.5 w-0.5 bg-ink-lo align-middle" /> seat norm{" "}
          {percentOneDecimal(expected)}
        </span>
        <span className="text-signal-red">
          <span className="mr-1 inline-block h-2.5 w-2 bg-signal-red align-middle" /> {name}{" "}
          {percentOneDecimal(rate)}
        </span>
      </p>
    </div>
  );
}

function tally(p: { yea: number; nay: number }): string {
  return `${p.yea} yea, ${p.nay} nay`;
}

/** One vote against the party, as the chamber recorded it: what was voted
 *  on (linked to the bill's page), the member's vote, and how each party
 *  split (the Congress record's counts, served with the vote). A vote
 *  stored before its roll call was recorded shows its bill and date alone. */
function BreakRow({ vote }: { vote: ListedBreak }) {
  const rc = vote.rollCall;
  const fallback = vote.billName ?? "";
  // The bill's short title from Congress.gov when the chamber titled the vote
  // by its official one (the chamber's stays on hover and the roll call page).
  const title = rc?.billShortTitle || voteTitle(rc?.title, rc?.billLabel, fallback);
  const href = voteSubjectHref(rc, vote.billId);
  const nominationLine = voteNominationLine(rc);
  const parties = rc?.parties.filter((p) => PARTY_SHORT[p.party] && p.yea + p.nay > 0) ?? [];
  return (
    <li className="flex flex-col gap-1 border-b border-white/[0.06] pb-2.5">
      <div className="flex items-start justify-between gap-3 text-sm">
        <span className="min-w-0 text-ink-hi">
          {href ? (
            // The bill's own page: its text, sponsor and every vote on it.
            <Link
              href={href}
              className="line-clamp-2 min-h-6 underline decoration-white/20 underline-offset-2 hover:text-phos"
              title={rc?.title || fallback}
            >
              {title}
            </Link>
          ) : (
            // A nomination or a procedural question: no bill to open, so
            // not clamped (a title attribute can't be read on a touch
            // screen, and two lines cut a nomination's office off).
            <span className="break-words">{title}</span>
          )}
          {nominationLine && (
            <span className="block text-[13px] text-ink-lo">{nominationLine}</span>
          )}
          {rc?.question && (
            <Link
              href={rollCallHref(rc)}
              className="block min-h-6 text-[13px] leading-6 text-ink-lo underline decoration-white/20 underline-offset-2 hover:text-phos"
            >
              {rc.question}
            </Link>
          )}
        </span>
        <span className="shrink-0 border border-signal-red/45 px-1.5 py-px font-mono text-xs text-signal-red">
          Voted {vote.vote.toLowerCase()}
        </span>
      </div>
      <span className="font-mono text-xs leading-relaxed text-ink-min">
        {rc ? (
          <Link
            href={`/congress/${rc.date}`}
            className="underline underline-offset-2 hover:text-phos"
          >
            {shortDate(rc.date)}
          </Link>
        ) : (
          vote.date
        )}
        {parties.map((p) => ` · ${PARTY_SHORT[p.party]} ${tally(p)}`)}
      </span>
    </li>
  );
}

export default function AlignmentColumn({
  memberId,
  chamber,
  name,
  seat,
  breaks,
  approval,
  dimension,
  onMore,
  note = null,
}: {
  memberId: string;
  chamber: "senate" | "house";
  /** How the scale names the member. */
  name: string;
  /** Where the norm comes from: "seats that lean like TN-2", "states that lean like Tennessee". */
  seat: string;
  /** Stored count of votes against the party (the voting record's): the
   *  list's count until the whole-Congress record is measured. */
  breaks: number;
  /** Survey approval among the member's own constituents, by party
   *  (informational, not scored); absent when the survey has none. */
  approval?: Approval | null;
  dimension: ScoreBreakdownDimension | undefined;
  onMore: () => void;
  /** A related line shown under the rate (votes with the president's
   *  stated positions). */
  note?: ReactNode;
}) {
  const facts = dimension?.facts as AlignmentFacts | undefined;
  // The breaks the score counts, served with it. Until the member's
  // whole-Congress record is measured, the stored votes against the party.
  const counted = facts?.breakVotes;
  const [stored, setStored] = useState<ListedBreak[] | null>(null);
  const [failed, setFailed] = useState(false);
  const votes: ListedBreak[] | null = counted ?? stored;
  const total = counted ? counted.length : breaks;
  const flank = facts?.flankBreakVotes ?? [];

  useEffect(() => {
    if (!dimension || counted || breaks === 0) return;
    let live = true;
    const fetcher = chamber === "house" ? fetchRepVotes : fetchSenatorVotes;
    fetcher(memberId, { category: "all", filter: "against-party", perPage: 100 })
      .then((r) => live && setStored(r.votes))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [memberId, chamber, breaks, dimension, counted]);

  return (
    <ScoreColumn
      title="Votes"
      shareId="constituent-alignment"
      tooltip={TOOLTIP}
      finding={dimension?.finding}
      more={{ label: "Every recorded vote", onClick: onMore }}
    >
      {facts ? (
        <Lede facts={facts} seat={seat} />
      ) : (
        <p className="text-base text-ink-lo">No party-line votes on record yet.</p>
      )}
      {facts?.breakRate != null && facts.expectedBreakRate != null && (
        <RateScale rate={facts.breakRate} expected={facts.expectedBreakRate} name={name} />
      )}
      {note}

      <Block label={`Votes against party (${total})`}>
        {total === 0 && <p className="text-sm text-ink-lo">None on record this Congress.</p>}
        {total > 0 && failed && <p className="text-sm text-ink-lo">Could not load the votes.</p>}
        {total > 0 && !failed && votes === null && (
          <p className="pulse-subtle font-mono text-xs text-ink-min">Loading votes...</p>
        )}
        {votes && votes.length > 0 && (
          <>
            <ul className="flex flex-col gap-2.5">
              {votes.slice(0, BREAKS_SHOWN).map((v, i) => (
                <BreakRow key={`${v.rollCall?.number ?? v.billName}-${i}`} vote={v} />
              ))}
            </ul>
            {votes.length > BREAKS_SHOWN && (
              <p className="font-mono text-xs text-ink-min">
                and {votes.length - BREAKS_SHOWN} more, in every recorded vote below
              </p>
            )}
          </>
        )}
        <details className="text-xs leading-relaxed text-ink-min">
          <summary className="inline-flex min-h-6 cursor-pointer items-center text-ink-lo hover:text-phos">
            What counts as a break
          </summary>
          <p className="mt-1">
            At least 65% of one party voted one way, at most 35% of the other did, and the member
            sided with the other party. Housekeeping votes (quorum calls, adjourning, approving the
            Journal, the House&apos;s previous question, motions to table or to recommit, the
            House&apos;s motion to commit) don&apos;t count.
            {counted &&
              " Each bill or nomination counts once, however many times it came to a vote."}
          </p>
        </details>
      </Block>

      {facts && flank.length > 0 && (
        <Block
          label={`From the ${FLANK[facts.party] ?? "party's"} flank, not counted (${flank.length})`}
        >
          <p className="text-xs leading-relaxed text-ink-min">
            On these votes the {PARTY_SHORT[facts.party] ?? "members"} who broke sit further from
            the {OTHER_PARTY[facts.party] ?? "other party"} than the party does, so they are not
            counted as breaks toward the other party.
          </p>
          <ul className="flex flex-col gap-2.5">
            {flank.slice(0, FLANK_SHOWN).map((v, i) => (
              <BreakRow key={`${v.rollCall?.number}-${i}`} vote={v} />
            ))}
          </ul>
          {flank.length > FLANK_SHOWN && (
            <p className="font-mono text-xs text-ink-min">and {flank.length - FLANK_SHOWN} more</p>
          )}
        </Block>
      )}

      <ConstituentApproval approval={approval} />
    </ScoreColumn>
  );
}
