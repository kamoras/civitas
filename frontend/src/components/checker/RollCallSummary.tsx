import Link from "next/link";
import type { VoteRollCall } from "@/types/senator";

const PARTY_NAME: Record<string, string> = { R: "Republicans", D: "Democrats", I: "Independents", ID: "Independents" };

/** A roll call as the chamber recorded it: what was voted on, when, and how
 *  each party voted — what shows whether a member's vote went against most
 *  of their party. Every number is the Congress record's (the API counts
 *  them); nothing is computed here. */
export default function RollCallSummary({ rollCall }: { rollCall: VoteRollCall }) {
  const parties = rollCall.parties.filter((p) => PARTY_NAME[p.party] && p.yea + p.nay > 0);
  return (
    <div className="space-y-1 text-xs">
      <p className="text-ink-lo">
        {rollCall.question}
        {rollCall.billLabel && <> &middot; {rollCall.billLabel}</>}
        {rollCall.result && <> &middot; {rollCall.result}</>}
      </p>
      <ul className="flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-ink-min" aria-label="How each party voted">
        {parties.map((p) => (
          <li key={p.party}>
            {PARTY_NAME[p.party]}: {p.yea} yea, {p.nay} nay
          </li>
        ))}
      </ul>
      <p className="flex flex-wrap gap-x-3 font-mono">
        <Link href={`/congress/${rollCall.date}`} className="text-ink-lo hover:text-phos underline underline-offset-2">
          {rollCall.chamber === "house" ? "House" : "Senate"} roll call {rollCall.number}, {rollCall.date}
        </Link>
        {rollCall.sourceUrl && (
          <a href={rollCall.sourceUrl} target="_blank" rel="noopener noreferrer" className="text-ink-lo hover:text-phos">
            Official record &#8599;
          </a>
        )}
      </p>
    </div>
  );
}
