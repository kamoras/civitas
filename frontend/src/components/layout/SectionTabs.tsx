import Link from "next/link";
import { BOXED_CONTROL, TAB_CONTROL } from "@/lib/controlStyles";
import {
  SECTIONS,
  tabChain,
  type SectionKey,
  type SectionPage,
  type SectionTabKey,
} from "@/lib/routes";

/** A section's sub-pages (`SECTIONS` in lib/routes.ts), each a page of its
 * own: plain links with `aria-current`, so each is linkable and renders on
 * the server. Every target is 44 px tall (WCAG 2.5.8 asks 24).
 *
 * From 640px up the links sit in a row that wraps rather than scrolls, so no
 * tab is off-screen and nothing widens the page. On a phone that row wrapped
 * to two or three rows of 16px caps (130-250px of the first screen), so under
 * 640px it is one 44px row naming the section and the page you are on
 * ("Elections ▸ Ballots ▾"), which opens the other pages beneath it
 * (mobile spec P3). A native disclosure of links rather than a <select>: a
 * select that navigates on change moves a reader to another page when they
 * only meant to look at the list (WCAG 3.2.2), and links keep their URLs.
 *
 * A tab with views (Reports: Floor and Members) adds a segmented row of them
 * under the bar at every width, and the open view's own views a second group
 * beside it (By the numbers, Votes with the president). A view that draws
 * its own toggle (the floor's Day, Week and Month: PeriodNav) adds nothing.
 * `active` is any page's key: the deepest page with it is marked "page",
 * the tab and view above it "true". */
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
  const chain = tabChain(section, active);
  const current = chain[0];
  const mark = (t: SectionPage, level: number) =>
    chain[level] === t ? (level === chain.length - 1 ? "page" : "true") : undefined;
  const link = (t: SectionPage, extra: string) => {
    const on = mark(t, 0);
    return (
      <Link
        key={t.key}
        href={t.href}
        aria-current={on}
        className={`flex min-h-11 items-center font-mono transition-colors ${extra} ${
          on ? TAB_CONTROL.selected : TAB_CONTROL.unselected
        }`}
      >
        {t.label}
      </Link>
    );
  };
  const view = chain[1];
  return (
    <nav aria-label={`${label} sections`} className={className}>
      {/* A phone: one row, the other pages under it when opened. A section
          with one page has nothing to switch to, so it is a plain label. */}
      {tabs.length > 1 ? (
        <details className="group border-b border-white/15 sm:hidden" data-section-switcher="">
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 font-mono text-[0.8125rem] [&::-webkit-details-marker]:hidden">
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
              <li key={t.key}>{link(t, "border-l-2 pl-3 text-[0.8125rem] ")}</li>
            ))}
          </ul>
        </details>
      ) : (
        <p className="flex min-h-11 items-center gap-2 border-b border-white/15 font-mono text-[0.8125rem] sm:hidden">
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
        <span className="flex min-h-11 items-center font-mono text-xs text-ink-min">{label}</span>
        {tabs.map((t) => link(t, "-mb-px border-b-2 text-sm "))}
      </div>
      {current?.views && (
        <div className="mt-2 flex flex-wrap items-center gap-x-6 gap-y-2" data-section-views="">
          <ViewGroup
            label={`${current.label} pages`}
            views={current.views}
            mark={(v) => mark(v, 1)}
          />
          {view?.views && !view.ownToggle && (
            <ViewGroup
              label={`${view.label} pages`}
              views={view.views}
              mark={(v) => mark(v, 2)}
              boxed
            />
          )}
        </div>
      )}
    </nav>
  );
}

/** A row of a tab's views, links with the one shown marked: underlined
 * under the bar, a segmented control for a view's own pages, so the two
 * levels never read as one. */
function ViewGroup({
  label,
  views,
  mark,
  boxed = false,
}: {
  label: string;
  views: readonly SectionPage[];
  mark: (v: SectionPage) => "page" | "true" | undefined;
  boxed?: boolean;
}) {
  return (
    <div role="group" aria-label={label} className={`flex flex-wrap ${boxed ? "" : "gap-x-5"}`}>
      {views.map((v) => {
        const on = mark(v);
        const look = boxed
          ? `-ml-px border px-3 first:ml-0 ${on ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected}`
          : `border-b-2 ${on ? TAB_CONTROL.selected : TAB_CONTROL.unselected}`;
        return (
          <Link
            key={v.key}
            href={v.href}
            aria-current={on}
            className={`flex min-h-11 items-center font-mono text-sm transition-colors ${look}`}
          >
            {v.label}
          </Link>
        );
      })}
    </div>
  );
}
