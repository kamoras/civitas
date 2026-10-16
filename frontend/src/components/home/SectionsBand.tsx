import Link from "next/link";
import { connection } from "next/server";
import { SECTIONS, type SectionKey } from "@/lib/routes";
import { sectionPreview } from "@/lib/sectionPreviews";
import { fetchSectionFigures } from "@/components/sections/SectionCards";

/** The sections the band lists: every one with a figure to show. About is
 * the methods, one link away in the menu and the footer. */
const BAND: SectionKey[] = [
  "congress",
  "elections",
  "agencies",
  "action",
  "politicians",
  "explore",
];

/**
 * The homepage's directory of the site: each section and every one of its
 * pages, with the page's live figure beside it (GET /api/sections, the same
 * figures the section hubs' cards show). A page added to SECTIONS appears
 * here with no change to this file. Rendered on the server with the page;
 * when the backend can't answer, the links stand without figures.
 */
export default async function SectionsBand() {
  // Per request, never prerendered: `next build` can't reach the backend.
  await connection();
  const figures = await fetchSectionFigures();
  return (
    <section aria-labelledby="sections-heading" className="mx-auto mt-10 max-w-7xl px-4 sm:px-6">
      <h2
        id="sections-heading"
        className="border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        Sections
      </h2>
      <div className="grid grid-cols-1 gap-x-9 gap-y-6 pt-4 sm:grid-cols-2 lg:grid-cols-3">
        {BAND.map((key) => {
          const { label, href, tabs } = SECTIONS[key];
          return (
            <div key={key}>
              <h3>
                <Link
                  href={href}
                  className="inline-flex min-h-6 items-center font-mono text-sm uppercase tracking-[0.14em] text-ink-hi hover:text-phos"
                >
                  {label}
                </Link>
              </h3>
              <ul className="mt-1 border-l border-white/15 pl-3">
                {tabs.map((t) => {
                  const p = sectionPreview(figures, key, t.key);
                  return (
                    <li key={t.key} className="py-1">
                      <div className="flex items-baseline justify-between gap-3">
                        <Link
                          href={t.href}
                          className="inline-flex min-h-6 items-center text-sm text-ink underline decoration-white/20 underline-offset-4 hover:text-phos"
                        >
                          {t.label}
                        </Link>
                        {p && (
                          <span className="shrink-0 font-mono text-sm tabular-nums text-ink-hi">
                            {p.figure}
                          </span>
                        )}
                      </div>
                      {p && <p className="text-xs leading-snug text-ink-min">{p.caption}</p>}
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
