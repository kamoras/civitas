import Link from "next/link";
import type { AgencyDocument } from "@/types/agency";
import { describeDaysLeftOn, formatUtcDate, safeHref } from "@/lib/formatting";

const day = (iso: string) =>
  formatUtcDate(iso, { year: "numeric", month: "short", day: "numeric" }, "en-US");

/** Documents as a list: the title opens Civitas's page for it (full text,
 * citation, comment help); an open comment period shows its deadline and a
 * link to comment where the Register says to. */
export default function AgencyDocList({
  docs,
  today,
  showDeadline = false,
}: {
  docs: AgencyDocument[];
  /** YYYY-MM-DD in the comment-deadline zone, from the server. */
  today: string;
  showDeadline?: boolean;
}) {
  return (
    <ul className="divide-y divide-white/[0.07]">
      {docs.map((d) => (
        <li key={d.id} className="py-3">
          <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
            {d.docType} · {day(d.date)}
          </p>
          <Link
            href={`/explore/${d.id}`}
            className="mt-1 block text-sm leading-snug text-ink-hi hover:text-phos"
          >
            {d.title}
          </Link>
          {showDeadline && d.commentsCloseOn && (
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className="text-signal-cyan">
                Comments close {day(d.commentsCloseOn)} (
                {describeDaysLeftOn(d.commentsCloseOn, today)})
              </span>
              {d.commentUrl && (
                <a
                  href={safeHref(d.commentUrl) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-6 items-center font-mono uppercase tracking-[0.12em] text-signal-cyan underline underline-offset-2 hover:text-phos"
                >
                  Comment at regulations.gov
                </a>
              )}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
