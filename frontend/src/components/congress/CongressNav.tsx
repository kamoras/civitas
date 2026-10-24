import Link from "@/components/Link";
import { BOXED_CONTROL } from "@/lib/controlStyles";

interface Step {
  href: string;
  label: string;
}

type Period = "congress" | "day" | "week" | "month";

/** The floor's four views, This Congress | Day | Week | Month, and the
 *  previous and next period. Every view links to the others, so a reader
 *  who opens a day can get back to the whole Congress from the same row. */
export function PeriodNav({
  active,
  hrefs,
  previous = null,
  next = null,
}: {
  active: Period;
  hrefs: Record<Period, string>;
  previous?: Step | null;
  next?: Step | null;
}) {
  const seg = (key: Period, label: string) => (
    <Link
      href={hrefs[key]}
      aria-current={active === key ? "page" : undefined}
      className={`flex min-h-11 items-center justify-center border px-4 font-mono text-small transition-colors ${
        active === key ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
      }`}
    >
      {label}
    </Link>
  );
  const step = (s: Step | null, arrow: "prev" | "next") =>
    s ? (
      <Link
        href={s.href}
        className="flex min-h-11 items-center border border-white/15 px-3 font-mono text-small text-ink-lo transition-colors hover:border-white/30 hover:text-ink-hi"
      >
        {arrow === "prev" ? `← ${s.label}` : `${s.label} →`}
      </Link>
    ) : null;
  return (
    <div className="@container flex flex-wrap items-center justify-between gap-3">
      <div
        role="group"
        aria-label="Report period"
        className="grid grid-cols-2 @min-[28rem]:grid-cols-4"
      >
        {seg("congress", "This Congress")}
        {seg("day", "Day")}
        {seg("week", "Week")}
        {seg("month", "Month")}
      </div>
      <div className="flex flex-wrap gap-2">
        {step(previous, "prev")}
        {step(next, "next")}
      </div>
    </div>
  );
}
