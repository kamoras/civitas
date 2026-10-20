import Link from "next/link";
import type { RegistrationClosing } from "@/types/thisWeek";
import { shortDate } from "@/lib/congress";
import { CALENDAR_HREF } from "@/lib/routes";

/** The states closing registration soon, as one line to the calendar (the
 * homepage and /elections): it matters before the election, but it is not
 * what either page is for. The full list, by day, is on the calendar
 * (ClosingSoon). Every date and count is the backend's. */
export default function ClosingLine({
  rows,
  days = 10,
  className = "mt-3",
}: {
  rows: RegistrationClosing[];
  days?: number;
  className?: string;
}) {
  if (rows.length === 0) return null;
  const soonest = rows.reduce((a, r) => (r.closesOn < a.closesOn ? r : a));
  const when =
    soonest.daysLeft === 0
      ? "today"
      : soonest.daysLeft === 1
        ? "tomorrow"
        : shortDate(soonest.closesOn);
  return (
    <p
      className={`${className} border border-white/30 border-l-4 border-l-signal-orange bg-surface px-4 py-2 text-sm`}
    >
      <Link
        href={CALENDAR_HREF}
        className="inline-flex min-h-6 items-center font-semibold text-ink-hi underline underline-offset-2 hover:text-phos"
      >
        Registration closes in the next {days} days in {rows.length}{" "}
        {rows.length === 1 ? "state" : "states"}, soonest {when} ▸
      </Link>
    </p>
  );
}
