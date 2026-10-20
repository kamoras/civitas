import Link from "next/link";
import type { CommitteeEntry } from "@/types/congress";
import { CHAMBER_NAME, billHref, dayHref, shortDate } from "@/lib/congress";

/**
 * Committee meetings and reports, each as the Daily Digest printed it: the
 * day (a link to that day's report), the chamber, the committee or the
 * report's number, the Digest's own sentence, and every bill it names as a
 * link to the bill's page. Shared by /congress/committees and a bill's page.
 */
export default function CommitteeEntries({ entries }: { entries: CommitteeEntry[] }) {
  return (
    <ul className="flex flex-col">
      {entries.map((e) => (
        <li
          key={e.id}
          className="grid grid-cols-1 gap-x-6 gap-y-1 border-b border-white/[0.09] py-3 sm:grid-cols-[9rem_minmax(0,1fr)]"
        >
          <p className="font-mono text-xs leading-relaxed tracking-[0.08em] text-ink-min">
            <Link
              href={dayHref(e.date)}
              className="text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
            >
              {shortDate(e.date).toUpperCase()}, {e.date.slice(0, 4)}
            </Link>
            <span className="sm:block">
              <span className="sm:hidden"> · </span>
              {CHAMBER_NAME[e.chamber].toUpperCase()} ·{" "}
              {e.kind === "meeting" ? "MEETING" : "REPORT"}
            </span>
          </p>
          <div className="min-w-0">
            {(e.committee || e.report) && (
              <p className="text-[15px] font-semibold text-ink">{e.committee ?? e.report}</p>
            )}
            <p className="text-sm leading-relaxed text-ink-lo">{e.text}</p>
            {e.bills.length > 0 && (
              <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-xs">
                {e.bills.map((b) => (
                  <Link
                    key={b.billId}
                    href={billHref(b.billId, e.congress)}
                    // 24px tall, so the row of bill numbers meets WCAG 2.2's
                    // target size on a phone.
                    className="inline-flex min-h-6 items-center text-phos-mid underline decoration-white/30 underline-offset-4 hover:text-phos"
                  >
                    {b.billLabel}
                  </Link>
                ))}
              </p>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
