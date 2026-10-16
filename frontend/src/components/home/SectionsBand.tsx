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

/** The researcher's way in: the methods, the API and the scoring changelog
 * (the sources are the ledger beside the index). They were a second line
 * of links in the masthead; one index holds every way in now. */
const METHODS_LINKS: readonly { href: string; label: string }[] = [
  { href: "/about/scores", label: "How scores are computed" },
  { href: "/developers", label: "Data & API" },
  { href: "/changelog", label: "Scoring changelog" },
];

const SECTION_LINK =
  "inline-flex min-h-6 items-center font-mono text-sm uppercase tracking-[0.14em] text-ink-hi hover:text-phos";
const PAGE_LINK =
  "inline-flex min-h-6 items-center text-sm text-ink underline decoration-white/20 underline-offset-4 hover:text-phos";

/**
 * The homepage's one index: every way into the site, gathered at the foot
 * of the page, after the day's record. The homepage's directory of the site: each section and every one of its
 * pages, with the page's live figure beside it (GET /api/sections, the same
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
        className="border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        Index
      </h2>
      <div className="grid grid-cols-1 gap-x-9 gap-y-6 pt-4 sm:grid-cols-2 xl:grid-cols-3">
        {BAND.map((key) => {
          const { label, href, tabs } = SECTIONS[key];
          return (
            <div key={key}>
              <h3>
                <Link href={href} className={SECTION_LINK}>
                  {label}
                </Link>
              </h3>
              <ul className="mt-1 border-l border-white/15 pl-3">
                {tabs.map((t) => {
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
        <div>
          <h3 className="inline-flex min-h-6 items-center font-mono text-sm uppercase tracking-[0.14em] text-ink-hi">
            Methods &amp; data
          </h3>
          <ul className="mt-1 border-l border-white/15 pl-3">
            {METHODS_LINKS.map(({ href, label }) => (
              <li key={href} className="py-1">
                <Link href={href} className={PAGE_LINK}>
                  {label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
