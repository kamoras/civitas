import Link from "next/link";
import type { IssueBallot } from "@/types/action";
import { stateBallotHref } from "@/lib/elections";
import { safeHref } from "@/lib/formatting";
import RegisterLink, { UNCONFIRMED } from "@/components/elections/RegisterLink";

const LINK =
  "inline-flex min-h-6 items-center text-signal-cyan underline decoration-signal-cyan/40 underline-offset-4 hover:text-phos";

function raceText(race: IssueBallot["races"][number], state: string): string {
  const seat = race.label.startsWith(`${state}-`) ? race.label : `${race.label} race`;
  return race.candidate ? `${seat} (${race.candidate})` : seat;
}

function daysLeft(n: number | null): string {
  if (n === null) return "";
  return n === 0 ? " (last day)" : ` (${n} day${n === 1 ? "" : "s"} left)`;
}

function Lookup({ lookup, stateName }: { lookup: IssueBallot["lookup"]; stateName: string }) {
  const href = safeHref(lookup?.url);
  if (!lookup || !href) return null;
  const label = lookup.isStateSpecific ? "Check your registration" : "Find your election office";
  const where = lookup.isStateSpecific ? `${stateName}'s official lookup` : lookup.sourceName;
  return (
    <>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={LINK}
        aria-label={`${label}: ${where}${lookup.unconfirmed ? ", unconfirmed" : ""} (opens in new tab)`}
      >
        {label} ↗
      </a>
      {lookup.unconfirmed && <span className="text-xs text-ink-lo">{UNCONFIRMED}</span>}
    </>
  );
}

/**
 * The ballot an issue reaches: for each state it names, the races it names
 * (each opening on the state's ballot page), the voter calendar's next
 * deadline (registration while it is open) and the state's own register
 * and lookup links. Matched from the issue's own words when it was written
 * and served with today's deadlines
 * (backend app/ballot_links.py); a state named with no race reads "Voting
 * in ...", never "on the ballot".
 */
export default function OnTheBallot({ ballot }: { ballot: IssueBallot[] | undefined }) {
  if (!ballot?.length) return null;
  return (
    <ul className="mb-1">
      {ballot.map((b) => (
        <li
          key={b.state}
          className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-b border-white/[0.07] py-3 text-[15px] text-ink"
        >
          <span className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
            {b.races.length ? "On the ballot" : "Voting in"}
          </span>
          <Link
            href={stateBallotHref(b.state)}
            className="font-display text-ink-hi underline decoration-ink-min/60 underline-offset-4 hover:decoration-ink-hi"
          >
            {b.stateName}
          </Link>
          {b.races.map((r) => (
            <span key={r.id} className="contents">
              <span aria-hidden="true" className="text-ink-min">
                ·
              </span>
              <Link href={`${stateBallotHref(b.state)}#race-${r.id}`} className={LINK}>
                {raceText(r, b.state)}
              </Link>
            </span>
          ))}
          {b.deadline && (
            <>
              <span aria-hidden="true" className="text-ink-min">
                ·
              </span>
              <Link href={`${stateBallotHref(b.state)}#voter-deadlines`} className={LINK}>
                {b.deadline.sentence}
                {daysLeft(b.deadline.daysLeft)}
              </Link>
              {b.registerLink && b.deadline.kind === "registration" && (
                <RegisterLink link={b.registerLink} showNote={false} />
              )}
            </>
          )}
          <span aria-hidden="true" className="text-ink-min">
            ·
          </span>
          <Lookup lookup={b.lookup} stateName={b.stateName} />
        </li>
      ))}
    </ul>
  );
}
