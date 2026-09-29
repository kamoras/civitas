import Link from "next/link";
import { TAB_CONTROL, BOXED_CONTROL } from "@/lib/controlStyles";

/** Reports | Bills: each a page of its own, so each is linkable and
 * renders on the server. */
export function CongressTabs({ active }: { active: "reports" | "bills" }) {
  const tab = (label: string, href: string, on: boolean) => (
    <Link
      href={href}
      aria-current={on ? "page" : undefined}
      className={`border-b-2 py-3 font-mono text-sm uppercase tracking-[0.14em] transition-colors ${
        on ? TAB_CONTROL.selected : TAB_CONTROL.unselected
      }`}
    >
      {label}
    </Link>
  );
  return (
    <nav
      aria-label="Congress sections"
      className="mb-8 flex items-end gap-8 border-b border-white/15"
    >
      <span className="py-3 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
        Congress
      </span>
      {tab("Reports", "/congress", active === "reports")}
      {tab("Bills", "/congress/bills", active === "bills")}
    </nav>
  );
}

interface Step {
  href: string;
  label: string;
}

/** Day | Week | Month, and the previous and next period. */
export function PeriodNav({
  active,
  hrefs,
  previous,
  next,
}: {
  active: "day" | "week" | "month";
  hrefs: Record<"day" | "week" | "month", string>;
  previous: Step | null;
  next: Step | null;
}) {
  const seg = (key: "day" | "week" | "month", label: string) => (
    <Link
      href={hrefs[key]}
      aria-current={active === key ? "page" : undefined}
      className={`flex min-h-11 min-w-20 items-center justify-center border font-mono text-sm uppercase tracking-[0.12em] transition-colors ${
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
        className="flex min-h-11 items-center border border-white/15 px-3 font-mono text-sm text-ink-lo transition-colors hover:border-white/30 hover:text-ink-hi"
      >
        {arrow === "prev" ? `← ${s.label}` : `${s.label} →`}
      </Link>
    ) : null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div role="group" aria-label="Report period" className="grid grid-cols-3">
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
