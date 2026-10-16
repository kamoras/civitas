import Link from "next/link";
import { SECTIONS, type SectionKey } from "@/lib/routes";
import { absoluteUrl } from "@/lib/site";
import { sectionPreview } from "@/lib/sectionPreviews";
import { shareMetaAttrs } from "@/lib/shareImage";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import type { SectionFigures } from "@/types/sections";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer (api/sections.py).
const REVALIDATE_S = 300;

/** GET /api/sections, or null when the backend can't answer: the cards
 * and the band then show their links without figures. */
export async function fetchSectionFigures(): Promise<SectionFigures | null> {
  try {
    const res = await fetch(`${BACKEND}/api/sections`, { next: { revalidate: REVALIDATE_S } });
    return res.ok ? ((await res.json()) as SectionFigures) : null;
  } catch {
    return null;
  }
}

/** Where each hub's figures come from, printed under a shared picture. */
const SOURCES: Partial<Record<SectionKey, string>> = {
  congress: "Congressional Record; Senate and House roll calls; OMB statements of policy",
  elections:
    "State election offices (official counts for the archive's map check); Civitas election map; enacted House maps",
  agencies: "Federal Register; USAspending.gov",
};

/**
 * A section hub's other pages, each a card with one live figure from the
 * backend (GET /api/sections, worded by lib/sectionPreviews.ts), so a page
 * a reader didn't know about announces itself with what it holds. Rendered
 * on the server with the page; one share button takes the whole grid,
 * source line included.
 *
 * Each card's link is its heading; the card's whole box is the target
 * (the link's ::after covers it), so the accessible name stays the page's
 * name rather than every word on the card.
 */
export default async function SectionCards({
  section,
  current,
  className = "mt-8",
}: {
  section: SectionKey;
  /** The hub page these cards sit on: left out of its own grid. */
  current: string;
  className?: string;
}) {
  const figures = await fetchSectionFigures();
  const { label, href } = SECTIONS[section];
  const cards = SECTIONS[section].tabs.filter((t) => t.key !== current);
  const headingId = `${section}-pages-heading`;
  return (
    <ShareSubjectProvider
      subject={{
        title: label,
        subtitle: `${label} on Civitas, by page`,
        url: absoluteUrl(href.split("?")[0]),
        source: SOURCES[section] ?? "Civitas",
        asOf: null,
      }}
    >
      <section
        aria-labelledby={headingId}
        data-share-section={`${section}-pages`}
        {...shareMetaAttrs({ source: SOURCES[section] ?? "Civitas", asOf: null })}
        className={`${className} scroll-mt-28`}
      >
        <div className="flex items-baseline justify-between gap-3 border-b border-white/15 pb-2">
          <h2 id={headingId} className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
            More in {label}
          </h2>
          <ShareSectionButton label={`${label} pages`} />
        </div>
        <ul
          className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[repeat(auto-fit,minmax(13rem,1fr))]"
          role="list"
        >
          {cards.map((t) => {
            const p = sectionPreview(figures, section, t.key);
            return (
              <li
                key={t.key}
                className="group relative flex flex-col border border-white/[0.07] border-t-white/15 bg-surface px-4 py-3 transition-colors focus-within:border-phos/40 hover:border-phos/40"
              >
                <h3 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-hi">
                  <Link
                    href={t.href}
                    className="after:absolute after:inset-0 group-hover:text-phos"
                  >
                    <span aria-hidden="true" className="mr-1.5 text-ink-min">
                      &rsaquo;
                    </span>
                    {t.label}
                  </Link>
                </h3>
                {p ? (
                  <p className="mt-2">
                    <span className="block font-display text-3xl font-extrabold leading-none tabular-nums text-ink-hi">
                      {p.figure}
                    </span>
                    <span className="mt-1.5 block font-sans text-sm leading-snug text-ink-lo">
                      {p.caption}
                    </span>
                  </p>
                ) : (
                  <p className="mt-2 text-sm leading-snug text-ink-min">
                    {figures ? "Open the page" : "Live figure unavailable right now"}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </ShareSubjectProvider>
  );
}
