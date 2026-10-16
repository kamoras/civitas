import Link from "next/link";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { ACCOUNTABILITY_SOURCE } from "@/lib/electionMap";
import { SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import { electionArchiveHref, ELECTIONS_ARCHIVE_HREF } from "@/lib/routes";
import type { ArchiveIndex } from "@/types/electionArchive";

/** The card's words, from the archive index's own counts (the backend's).
 * Before any election with a kept map, it says when the first check comes:
 * an empty card is content, never a row of zeros. */
export function accountabilityCard(index: ArchiveIndex): {
  headline: string;
  detail: string;
  href: string;
} {
  const latest = index.cycles.find((c) => c.accountability);
  const a = latest?.accountability;
  if (!latest || !a) {
    return {
      headline: "The seat map checks itself",
      detail: `After the ${index.current.cycle} election, each official margin is checked against the 80% and 95% ranges the map published, misses included, and each seat's swing from its usual vote is mapped.`,
      href: ELECTIONS_ARCHIVE_HREF,
    };
  }
  const href = `${electionArchiveHref(latest.cycle)}#map-check`;
  if (a.graded === 0) {
    return {
      headline: `${latest.cycle}: no count official yet`,
      detail: `0 of ${a.ranged} seats with a range have an official count. Each is checked against the map's ranges once its state lists the count official.`,
      href,
    };
  }
  return {
    headline: `${latest.cycle}: ${a.held80} of ${a.graded} official margins inside the map's 80% ranges`,
    detail: `${a.held95} of ${a.graded} inside the 95% ranges; ${a.certified} of ${a.ranged} seats with a range have an official count${latest.stillCounting ? " so far" : ""}. With the swing map of ${a.swingSeats} ${a.swingSeats === 1 ? "seat" : "seats"}.`,
    href,
  };
}

/** The elections hub's card for the map's public check (the archive page's
 * "Did the map's ranges hold?"), share-ready with its source line. */
export default function AccountabilityCard({ index }: { index: ArchiveIndex }) {
  const card = accountabilityCard(index);
  return (
    <ShareSubjectProvider
      subject={{
        title: "Did the seat map's ranges hold?",
        url: absoluteUrl(card.href),
        source: ACCOUNTABILITY_SOURCE,
        asOf: null,
      }}
    >
      <section
        id="map-check-card"
        aria-labelledby="map-check-card-title"
        className="mt-10 border border-phos/20 bg-surface p-4"
        {...{ [SHARE_SECTION_ATTR]: "map-check-card" }}
        {...shareMetaAttrs({ source: ACCOUNTABILITY_SOURCE, asOf: null })}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-min">
              Map against the count
            </p>
            <h2
              id="map-check-card-title"
              className="mt-1 font-display text-lg font-bold text-ink-hi"
            >
              {card.headline}
            </h2>
          </div>
          <ShareSectionButton label="Did the seat map's ranges hold?" anchor="map-check-card" />
        </div>
        <p className="mt-2 max-w-3xl text-sm text-ink-lo">{card.detail}</p>
        <Link
          href={card.href}
          className="mt-3 inline-flex min-h-11 items-center font-mono text-xs tracking-[0.12em] text-phos underline underline-offset-4"
        >
          THE CHECK AND THE SWING MAP →
        </Link>
        <p className="mt-3 border-t border-white/[0.07] pt-2 font-mono text-[11px] leading-relaxed text-ink-min">
          Source: {ACCOUNTABILITY_SOURCE}.
        </p>
      </section>
    </ShareSubjectProvider>
  );
}
