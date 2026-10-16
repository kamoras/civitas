import Link from "next/link";
import { TAB_CONTROL } from "@/lib/controlStyles";
import { SECTIONS, type SectionKey, type SectionTabKey } from "@/lib/routes";

/** A section's sub-pages (`SECTIONS` in lib/routes.ts), each a page of its
 * own: plain links with `aria-current`, so each is linkable and renders on
 * the server. Rows wrap rather than scroll, so no tab is off-screen at
 * 375 px and nothing widens the page; every target is 44 px tall (WCAG
 * 2.5.8 asks 24). */
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
  return (
    <nav
      aria-label={`${label} sections`}
      className={`${className} flex flex-wrap items-end gap-x-5 border-b border-white/15 sm:gap-x-8`}
    >
      <span className="flex min-h-11 items-center font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
        {label}
      </span>
      {tabs.map((t) => {
        const on = t.key === active;
        return (
          <Link
            key={t.key}
            href={t.href}
            aria-current={on ? "page" : undefined}
            className={`-mb-px flex min-h-11 items-center border-b-2 font-mono text-sm uppercase tracking-[0.14em] transition-colors ${
              on ? TAB_CONTROL.selected : TAB_CONTROL.unselected
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
