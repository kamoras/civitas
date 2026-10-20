import Link from "next/link";
import { TAB_CONTROL } from "@/lib/controlStyles";
import { SECTIONS, type SectionKey, type SectionTabKey } from "@/lib/routes";

/** A section's sub-pages (`SECTIONS` in lib/routes.ts), each a page of its
 * own: plain links with `aria-current`, so each is linkable and renders on
 * the server. Every target is 44 px tall (WCAG 2.5.8 asks 24).
 *
 * From 640px up the links sit in a row that wraps rather than scrolls, so no
 * tab is off-screen and nothing widens the page. On a phone that row wrapped
 * to two or three rows of 16px caps (130-250px of the first screen), so under
 * 640px it is one 44px row naming the section and the page you are on
 * ("Elections ▸ Ballots by state ▾"), which opens the other pages beneath it
 * (mobile spec P3). A native disclosure of links rather than a <select>: a
 * select that navigates on change moves a reader to another page when they
 * only meant to look at the list (WCAG 3.2.2), and links keep their URLs. */
export default function SectionTabs<S extends SectionKey>({
  section,
  active,
  className = "mb-8",
}: {
  section: S;
  active: SectionTabKey<S> | null;
  className?: string;
}) {
  const { label, tabs } = SECTIONS[section];
  const current = tabs.find((t) => t.key === active);
  const link = (t: (typeof tabs)[number], extra: string) => {
    const on = t.key === active;
    return (
      <Link
        key={t.key}
        href={t.href}
        aria-current={on ? "page" : undefined}
        className={`flex min-h-11 items-center font-mono uppercase transition-colors ${extra} ${
          on ? TAB_CONTROL.selected : TAB_CONTROL.unselected
        }`}
      >
        {t.label}
      </Link>
    );
  };
  return (
    <nav aria-label={`${label} sections`} className={className}>
      {/* A phone: one row, the other pages under it when opened. A section
          with one page has nothing to switch to, so it is a plain label. */}
      {tabs.length > 1 ? (
        <details className="group border-b border-white/15 sm:hidden" data-section-switcher="">
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 font-mono text-[0.8125rem] uppercase tracking-[0.08em] [&::-webkit-details-marker]:hidden">
            <span className="text-ink-min">{label}</span>
            <span aria-hidden="true" className="text-ink-min">
              ▸
            </span>
            <span className="min-w-0 truncate text-ink-hi">{current?.label ?? "Pages"}</span>
            <span aria-hidden="true" className="ml-auto text-ink-lo group-open:rotate-180">
              ▾
            </span>
          </summary>
          <ul className="flex flex-col pb-2">
            {tabs.map((t) => (
              <li key={t.key}>{link(t, "border-l-2 pl-3 text-[0.8125rem] tracking-[0.08em]")}</li>
            ))}
          </ul>
        </details>
      ) : (
        <p className="flex min-h-11 items-center gap-2 border-b border-white/15 font-mono text-[0.8125rem] uppercase tracking-[0.08em] sm:hidden">
          <span className="text-ink-min">{label}</span>
          <span aria-hidden="true" className="text-ink-min">
            ▸
          </span>
          {tabs[0] && link(tabs[0], "min-w-0 truncate border-b-0 text-ink-hi")}
        </p>
      )}
      <div
        className="hidden flex-wrap items-end gap-x-5 border-b border-white/15 sm:flex sm:gap-x-8"
        data-section-row=""
      >
        <span className="flex min-h-11 items-center font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
          {label}
        </span>
        {tabs.map((t) => link(t, "-mb-px border-b-2 text-sm tracking-[0.14em]"))}
      </div>
    </nav>
  );
}
