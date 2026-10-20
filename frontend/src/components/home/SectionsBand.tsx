import Link from "next/link";
import { connection } from "next/server";
import { SECTIONS, type SectionKey } from "@/lib/routes";
import { leadOrder, sectionPreview, seasonLead } from "@/lib/sectionPreviews";
import { fetchSectionFigures } from "@/components/sections/SectionCards";
import type { SectionFigures } from "@/types/sections";

/** Every section, About's chapters (the methods) included: the homepage
 * index is the one list of the site's pages outside the header. */
const BAND: SectionKey[] = [
  "congress",
  "elections",
  "executive",
  "court",
  "action",
  "explore",
  "about",
];

/** The three Congress reports, which on a day without business would each
 * read "None" with near-identical captions. */
const REPORT_TABS = new Set(["reports", "week", "month"]);

/** A day without business: the three reports as one line, the backend's
 * (sections.py `recess`), when none of them has a vote to count. */
export function reportsRecess(figures: SectionFigures | null) {
  const c = figures?.congress;
  return c?.recess && [c.reports, c.week, c.month].every((r) => !r || r.recordVotes === 0)
    ? c.recess
    : null;
}

function ReportsInRecess({ line }: { line: string }) {
  const reports = SECTIONS.congress.tabs.filter((t) => REPORT_TABS.has(t.key));
  return (
    <li className="py-1">
      <p className="flex flex-wrap gap-x-3">
        {reports.map((t) => (
          <Link key={t.key} href={t.href} className={PAGE_LINK}>
            {t.label}
          </Link>
        ))}
      </p>
      {/* Shown at every width: it stands in for three figures. */}
      <p className="text-xs leading-snug text-ink-min">{line}</p>
    </li>
  );
}

const SECTION_LINK =
  "inline-flex min-h-6 items-center font-mono text-sm text-ink-hi hover:text-phos";
const PAGE_LINK =
  "inline-flex min-h-6 items-center text-sm text-ink underline decoration-white/20 underline-offset-4 hover:text-phos";

/**
 * The homepage's one index: every section and every one of its pages,
 * after the day's record, with the page's live figure beside it (GET /api/sections, the same
 * figures the section hubs' cards show). A page added to SECTIONS appears
 * here with no change to this file. Rendered on the server with the page;
 * when the backend can't answer, the links stand without figures.
 *
 * On a phone each page shows its figure without its caption (kept for
 * screen readers): the list reads in one screen and a half rather than
 * two, and the captions return from 640 px up.
 */
export default async function SectionsBand() {
  // Per request, never prerendered: `next build` can't reach the backend.
  await connection();
  const figures = await fetchSectionFigures();
  return (
    <section aria-labelledby="sections-heading" className="min-w-0">
      <h2
        id="sections-heading"
        className="border-b border-white/15 pb-2 font-mono text-xs text-ink-min"
      >
        Index
      </h2>
      <div className="grid grid-cols-1 gap-x-9 gap-y-6 pt-4 sm:grid-cols-2 xl:grid-cols-3">
        {BAND.map((key) => {
          const { label, href, tabs } = SECTIONS[key];
          const recess = key === "congress" ? reportsRecess(figures) : null;
          return (
            <div key={key}>
              <h3>
                <Link href={href} className={SECTION_LINK}>
                  {label}
                </Link>
              </h3>
              <ul className="mt-1 border-l border-white/15 pl-3">
                {recess && <ReportsInRecess line={recess.line} />}
                {leadOrder([...tabs], seasonLead(figures, key)).map((t) => {
                  if (recess && REPORT_TABS.has(t.key)) return null;
                  const p = sectionPreview(figures, key, t.key);
                  return (
                    <li key={t.key} className="py-1">
                      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                        <Link href={t.href} className={PAGE_LINK}>
                          {t.label}
                        </Link>
                        {p && (
                          <span className="shrink-0 font-mono text-sm tabular-nums text-ink-hi">
                            {p.figure}
                          </span>
                        )}
                      </div>
                      {p && (
                        <p className="sr-only text-xs leading-snug text-ink-min sm:not-sr-only">
                          {p.caption}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}
