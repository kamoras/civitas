import Link from "next/link";
import { SECTION_HEADING } from "@/components/action/IssueEnrichment";
import { CHAMBER_NAME, rollCallHref, voteResult, voteTally } from "@/lib/congress";
import { formatUtcDate } from "@/lib/formatting";
import type { ActionIssue } from "@/types/action";

/**
 * "Related votes in Congress" on an issue: the roll calls the backend linked
 * to it (api/action.py issue_roll_calls: the vote a roll-call issue reports,
 * votes on a bill its coverage names, and votes matched to the story by
 * app/roll_call_links.py). Worded as related, never as the cause of the
 * story or the vote it reported: the match is two records' words and dates.
 */
export default function RelatedVotes({
  issue,
  headingLevel = "h3",
  className = "mt-8",
}: {
  issue: ActionIssue;
  headingLevel?: "h2" | "h3" | "h4";
  className?: string;
}) {
  const votes = issue.relatedVotes ?? [];
  if (votes.length === 0) return null;
  const Heading = headingLevel;
  return (
    <section className={className}>
      <Heading className={SECTION_HEADING}>Related votes in Congress</Heading>
      <p className="mt-2 text-[13px] text-ink-lo">
        Roll calls taken around this story on what it covers, matched by the words of their record.
        Pick your state below to see how your members voted.
      </p>
      <ul className="mt-1">
        {votes.map((v) => (
          <li
            key={`${v.chamber}-${v.congress}-${v.session}-${v.number}`}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 border-b border-white/[0.07] py-2.5"
          >
            <span className="min-w-0">
              <span className="block font-sans text-[15px] leading-snug text-ink-hi">
                {[v.billLabel, v.title || v.question].filter(Boolean).join(" · ")}
              </span>
              <span className="mt-0.5 block text-[13px] text-ink-lo">
                {CHAMBER_NAME[v.chamber]} ·{" "}
                {formatUtcDate(v.date, { month: "short", day: "numeric", year: "numeric" })} ·{" "}
                {voteResult(v)} {voteTally({ yeas: v.yeas ?? 0, nays: v.nays ?? 0 })}
              </span>
            </span>
            <Link
              href={rollCallHref(v)}
              className="whitespace-nowrap font-mono text-xs text-phos-mid underline-offset-4 hover:text-phos hover:underline"
              aria-label={`How each member voted: ${CHAMBER_NAME[v.chamber]} roll call ${v.number}`}
            >
              Roll call →
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
