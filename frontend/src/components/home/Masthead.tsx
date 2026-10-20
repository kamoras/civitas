import Link from "next/link";
import { ELECTION_MAP_HREF } from "@/lib/electionMap";
import { REDISTRICTING_HREF } from "@/lib/redistricting";
import type { ElectionPhase } from "@/types/election";

const VOTER_LINK =
  "inline-flex min-h-11 sm:min-h-[24px] items-center border-b border-phos-mid/40 text-phos-mid hover:text-phos";

/** The election pages the "For voters" line adds, by the election's phase
 * (the backend's, as ElectionNight reads it): the seat map and the new
 * House maps through the campaign; from election day the results, and the
 * new maps, which stay up. Nothing extra when the phase can't be read. */
export function electionLinks(phase: ElectionPhase | null): { href: string; label: string }[] {
  if (phase === "campaign")
    return [
      { href: ELECTION_MAP_HREF, label: "Seat map" },
      { href: REDISTRICTING_HREF, label: "New House maps" },
    ];
  if (phase === "election_day" || phase === "results")
    return [
      { href: "/elections", label: "Results" },
      { href: REDISTRICTING_HREF, label: "New House maps" },
    ];
  return [];
}

/**
 * The homepage masthead: the nameplate and its line, the statement of what this
 * is, and the voter's ways in. It asks the reader for nothing.
 *
 * Replaces a full-viewport centred hero over a canvas animation. That is
 * marketing-page anatomy — hero, then proof, then a call to action — and it
 * read as a product launch rather than a public-interest project. This is a
 * document header: what the record is and how to find something in it.
 *
 * No props and no form (2026-10, reader feedback that both cost the page
 * its seriousness, and that a homepage asking for input before it shows
 * anything takes away from it): the tilted rubber stamp is gone, its facts
 * stated once, plainly, in the source ledger ("Built from the public
 * record") and the footer; the "request a record" box is the header's
 * search, on every page; and the state select is the header's too, asked
 * once for every page, with the voter links following it.
 */
export default function Masthead({
  electionPhase = null,
}: {
  electionPhase?: ElectionPhase | null;
}) {
  const extra = electionLinks(electionPhase);
  return (
    <section className="mx-auto max-w-7xl px-4 pt-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0 max-w-2xl">
          <h1 className="font-display text-[30px] font-semibold leading-[1.1] text-ink-hi sm:text-[44px]">
            Who holds power, who pays for it, how they vote.
          </h1>
          <p className="mt-3 text-[17px] leading-relaxed text-ink">
            The public record of U.S. government, rebuilt every night from federal and state
            filings, with every number linked to the document it came from.
          </p>
          {/* What the site holds itself to, as one plain line: the rotated,
              bordered stamp that carried it was a novelty prop, removed in
              #1061 and kept out. */}
          <p className="mt-2 text-sm leading-relaxed text-ink-lo">
            No party money. No PAC money. No ads, no accounts. Open source, self-hosted, run by
            volunteers.
          </p>
          {extra.length > 0 && (
            <p className="mt-2 flex flex-wrap gap-x-4 text-sm">
              {extra.map(({ href, label }) => (
                <Link key={label} href={href} className={VOTER_LINK}>
                  {label.charAt(0) + label.slice(1).toLowerCase()}
                </Link>
              ))}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
