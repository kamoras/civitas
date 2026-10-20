import Link from "next/link";
import type { SupportRollCall } from "@/types/presidentialSupport";
import { shortDate, billPageHref, rollCallHref } from "@/lib/congress";

/** The anchor a tally square links to. */
export function voteAnchor(v: SupportRollCall): string {
  return `ps-vote-${v.chamber}-${v.congress}-${v.session}-${v.rollCall}`;
}

/**
 * The counted roll calls, newest first, each with the statement it was
 * counted against: a link to the signed PDF and the sentence the position
 * was read from, verbatim. With `member`, each row also says how the member
 * voted.
 */
export default function VoteList({ votes, member }: { votes: SupportRollCall[]; member?: string }) {
  return (
    <ol className="divide-y divide-white/[0.07] border-y border-white/[0.07]">
      {votes.map((v) => {
        const bill = billPageHref(v.billId, v.congress);
        return (
          <li key={voteAnchor(v)} id={voteAnchor(v)} className="py-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-xs text-ink-min">
              <span>{shortDate(v.date)}</span>
              {bill ? (
                <Link
                  href={bill}
                  className="inline-flex min-h-6 items-center text-ink-lo hover:text-phos"
                >
                  {v.billId}
                </Link>
              ) : null}
              <Link
                href={rollCallHref({ ...v, number: v.rollCall })}
                className="inline-flex min-h-6 items-center tracking-normal text-ink-lo underline decoration-white/20 underline-offset-2 hover:text-phos"
              >
                {v.question}
              </Link>
              <span>· {v.result}</span>
            </div>
            <p className="mt-1 font-sans text-sm text-ink">{v.statement.title}</p>
            <p className="mt-1 font-mono text-xs text-ink-lo">
              President&apos;s side: {v.presidentSide}
              {member && (
                <>
                  {" · "}
                  {member}: {v.memberVote ?? "No recorded vote"}
                  {v.withPresident === true && " · with"}
                  {v.withPresident === false && " · against"}
                  {v.withPresident == null && v.memberVote && " · not counted"}
                </>
              )}
            </p>
            <blockquote className="mt-2 border-l border-white/[0.09] pl-3 font-sans text-xs text-ink-lo">
              &ldquo;{v.statement.sentence}&rdquo;
            </blockquote>
            <p className="mt-1 flex flex-wrap gap-x-4 font-mono text-xs">
              <a
                href={v.statement.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-6 items-center text-signal-cyan hover:text-phos"
              >
                [Statement, {v.statement.issued}]
              </a>
              {v.sourceUrl && (
                <a
                  href={v.sourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-6 items-center text-signal-cyan hover:text-phos"
                >
                  [Roll call {v.rollCall}]
                </a>
              )}
            </p>
          </li>
        );
      })}
    </ol>
  );
}
