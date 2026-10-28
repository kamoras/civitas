"use client";

import { useEffect, useState } from "react";
import Link from "@/components/Link";
import { fetchRepVotes, fetchSenatorVotes } from "@/lib/api";
import { rollCallHref } from "@/lib/congress";
import { voteNominationLine, voteSubjectHref } from "@/lib/nominations";
import type { AlignmentFacts, BreakVote, ScoreBreakdownDimension } from "@/types/scoreBreakdown";
import type { MemberBox as Box } from "@/types/politicians";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import MemberBox, { Block } from "./MemberBox";
import { count, partyMembers, percentOneDecimal, shortDate, voteTitle } from "./format";

const PARTY_SHORT: Record<string, string> = { R: "Republicans", D: "Democrats" };
const FLANK: Record<string, string> = { R: "right", D: "left" };
const OTHER_PARTY: Record<string, string> = { R: "Democrats", D: "Republicans" };
// Breaks shown in the column; the drawer lists every vote.
const BREAKS_SHOWN = 6;
const FLANK_SHOWN = 3;

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
      <p className="text-body leading-relaxed text-ink">
        {count(facts.partyVotes, "party-line vote", "party-line votes")} on record so far: too few
        to compare with how often {members} in similar seats break with the party.
      </p>
    );
  }
  return (
    <p className="text-body leading-relaxed text-ink">
      Voted against most {members} on {facts.breaks} of {facts.partyVotes} party-line votes (
      {percentOneDecimal(facts.breakRate)}).
      {facts.expectedBreakRate != null &&
        ` ${members} in ${seat} do that on ${percentOneDecimal(facts.expectedBreakRate)}.`}
    </p>
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
  const title =
    rc?.measureTitle || rc?.billShortTitle || voteTitle(rc?.title, rc?.billLabel, fallback);
  const href = voteSubjectHref(rc, vote.billId);
  const nominationLine = voteNominationLine(rc);
  const parties = rc?.parties.filter((p) => PARTY_SHORT[p.party] && p.yea + p.nay > 0) ?? [];
  return (
    <li className="flex flex-col gap-1 border-b border-white/[0.06] pb-2.5">
      <div className="flex items-start justify-between gap-3 text-small">
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
          {nominationLine && <span className="block text-small text-ink-lo">{nominationLine}</span>}
          {rc?.amendmentPurpose && (
            <span className="block text-small text-ink-lo">
              {rc.amendment}: {rc.amendmentPurpose}
            </span>
          )}
          {rc?.question && (
            <Link
              href={rollCallHref(rc)}
              className="block min-h-6 text-small leading-6 text-ink-lo underline decoration-white/20 underline-offset-2 hover:text-phos"
            >
              {rc.question}
            </Link>
          )}
        </span>
        <span className="shrink-0 border border-signal-red/45 px-1.5 py-px font-mono text-small text-signal-red">
          Voted {vote.vote.toLowerCase()}
        </span>
      </div>
      <span className="font-mono text-small leading-relaxed text-ink-min">
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

export default function VotesBox({
  memberId,
  chamber,
  seat,
  breaks,
  box,
  dimension,
  onMore,
}: {
  memberId: string;
  chamber: "senate" | "house";
  /** Where the norm comes from: "seats that lean like TN-2", "states that lean like Tennessee". */
  seat: string;
  /** Stored count of votes against the party (the voting record's): the
   *  list's count until the whole-Congress record is measured. */
  breaks: number;
  box: Box | null | undefined;
  dimension: ScoreBreakdownDimension | undefined;
  onMore: () => void;
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
    <MemberBox
      id="constituent-alignment"
      label="Votes"
      box={box}
      about="The member's breaks with the party on this Congress's party-line votes: what each was a vote on and how each party split. How often, beside members of the same party in similar seats, is in the strip above. Only breaks toward the other party count; votes against the party from its own flank are listed apart."
      fallback={{ kicker: "Votes", title: "Votes", dek: dimension?.finding }}
      more={{
        label: "every break and how it is counted",
        children: (
          <div className="flex flex-col gap-4">
            {/* The rate against the seat is the peer strip's; the line
                stands in only for a backend older than the boxes. */}
            {!facts ? (
              <p className="text-body text-ink-lo">No party-line votes on record yet.</p>
            ) : (
              !box && <Lede facts={facts} seat={seat} />
            )}
            <Block label={`Votes against party (${total})`}>
              {total === 0 && (
                <p className="text-small text-ink-lo">None on record this Congress.</p>
              )}
              {total > 0 && failed && (
                <p className="text-small text-ink-lo">Could not load the votes.</p>
              )}
              {total > 0 && !failed && votes === null && (
                <p className="pulse-subtle text-small text-ink-min">Loading votes...</p>
              )}
              {votes && votes.length > 0 && (
                <>
                  <ul className="flex flex-col gap-2.5">
                    {votes.slice(0, BREAKS_SHOWN).map((v, i) => (
                      <BreakRow key={`${v.rollCall?.number ?? v.billName}-${i}`} vote={v} />
                    ))}
                  </ul>
                  {votes.length > BREAKS_SHOWN && (
                    <p className="text-small text-ink-min">
                      and {votes.length - BREAKS_SHOWN} more, in every recorded vote
                    </p>
                  )}
                </>
              )}
              <p className="text-body leading-relaxed text-ink-min">
                What counts as a break: a majority of one party voted one way, a majority of the
                other voted the other way, and the member sided with the other party. Housekeeping
                votes (quorum calls, adjourning, approving the Journal, the House&apos;s previous
                question, motions to table or to recommit, the House&apos;s motion to commit)
                don&apos;t count.
                {counted &&
                  " Each bill or nomination counts once, however many times it came to a vote."}
              </p>
            </Block>
            {facts && flank.length > 0 && (
              <Block
                label={`From the ${FLANK[facts.party] ?? "party's"} flank, not counted (${flank.length})`}
              >
                <p className="text-body leading-relaxed text-ink-min">
                  On these votes the {PARTY_SHORT[facts.party] ?? "members"} who broke sit further
                  from the {OTHER_PARTY[facts.party] ?? "other party"} than the party does, so they
                  are not counted as breaks toward the other party.
                </p>
                <ul className="flex flex-col gap-2.5">
                  {flank.slice(0, FLANK_SHOWN).map((v, i) => (
                    <BreakRow key={`${v.rollCall?.number}-${i}`} vote={v} />
                  ))}
                </ul>
                {flank.length > FLANK_SHOWN && (
                  <p className="text-small text-ink-min">and {flank.length - FLANK_SHOWN} more</p>
                )}
              </Block>
            )}
            <div className="flex flex-wrap items-center gap-x-4" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
              <button
                type="button"
                onClick={onMore}
                className="min-h-11 text-left text-small text-ink underline underline-offset-2 hover:text-phos"
              >
                Every recorded vote &rarr;
              </button>
              <ShareSectionButton label="Votes" />
            </div>
          </div>
        ),
      }}
    />
  );
}
