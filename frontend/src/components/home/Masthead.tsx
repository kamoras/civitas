import Link from "next/link";
import { ELECTION_MAP_HREF } from "@/lib/electionMap";
import { REDISTRICTING_HREF } from "@/lib/redistricting";
import type { ElectionPhase } from "@/types/election";

const VOTER_LINK =
  "inline-flex min-h-[24px] items-center border-b border-phos-mid/40 text-phos-mid hover:text-phos";

/** The election pages the "For voters" line adds, by the election's phase
 * (the backend's, as ElectionNight reads it): the seat map and the new
 * House maps through the campaign; from election day the results, and the
 * new maps, which stay up. Nothing extra when the phase can't be read. */
export function electionLinks(phase: ElectionPhase | null): { href: string; label: string }[] {
  if (phase === "campaign")
    return [
      { href: ELECTION_MAP_HREF, label: "SEAT MAP" },
      { href: REDISTRICTING_HREF, label: "NEW HOUSE MAPS" },
    ];
  if (phase === "election_day" || phase === "results")
    return [
      { href: "/elections", label: "RESULTS" },
      { href: REDISTRICTING_HREF, label: "NEW HOUSE MAPS" },
    ];
  return [];
}

/**
 * The homepage masthead: the overprint wordmark, the statement of what this
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
 * search, on every page; and the state select is gone from the voter line,
 * since each link leads to a page where the state is chosen.
 */
export default function Masthead({
  electionPhase = null,
}: {
  electionPhase?: ElectionPhase | null;
}) {
  return (
    <section className="mx-auto max-w-7xl px-4 pt-8 sm:px-6">
      <div className="min-w-0">
        {/* The two offset plates are drawn from `data-text` as pseudo-
              elements (see .overprint in globals.css), so the mark's text
              content stays exactly "CIVITAS" — copyable, findable and
              scrapeable once rather than three times.

              A nameplate, not the heading. It was the page's h1, which meant
              the homepage's one h1 was a brand mark carrying no information
              about the page, while the sentence that actually says what this
              is sat in a <p>. It is also the third "CIVITAS" in the accessible
              tree after the nav's home link and the document title, and the
              second on screen — the nav wordmark sits 86px directly above it.
              `aria-hidden` leaves it as the printed nameplate it looks like. */}
        <div
          aria-hidden="true"
          className="overprint font-pixel text-[min(1.5rem,12vw)] leading-none text-ink-hi sm:text-3xl"
          data-text="CIVITAS"
        >
          <span className="relative">CIVITAS</span>
        </div>

        {/* A statement of scope, not a slogan.

              The same three clauses were set at 43px extrabold uppercase over
              three lines, which is billboard treatment — the one place the
              voice slipped from register to pitch. The clauses are the honest
              description of what the record covers, so they stay; what goes is
              the shouting. A masthead states its remit and gets out of the
              way.

              The subject widened in 2026 and the sentence did not. "Every
              member of Congress" described the whole record when Congress was
              the whole record; it now also holds 47 presidencies, the nine
              sitting justices, 484 races with 4,316 candidates, 2,554
              state-legislative nominees and 26 ballot measures. A remit
              statement that names a third of the remit is not modest, it is
              inaccurate — and the people asking for power are half of what a
              voter comes here to check. */}
        <h1 className="mt-6 max-w-2xl font-display text-[26px] font-semibold leading-[1.22] tracking-[-0.015em] text-ink-hi sm:mt-7 sm:text-[32px]">
          A public record of who holds power and who wants it: who funds them, how they vote, and
          what they pass.
        </h1>

        <p className="mt-4 max-w-xl font-display text-base leading-relaxed text-ink-lo sm:text-[17px]">
          Rebuilt every night from federal and state filings. Every number links to the document it
          came from. Where a figure is an estimate, like the ranges on the{" "}
          <Link
            href={ELECTION_MAP_HREF}
            className="underline decoration-white/30 underline-offset-4 hover:text-phos"
          >
            election map
          </Link>
          , the page shows how it was made.
        </p>

        {/* The voter's way in, in the first screen. Each link leads to a
              page that asks for the state (§8: an explicit, unremembered
              choice there); a bare form select in the masthead read as a
              widget, and saved no step. The researcher's links (methods,
              API, changelog) are in the page's one index. */}
        <nav
          aria-label="For voters"
          className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-xs tracking-[0.1em]"
        >
          <Link href="/politicians" className={VOTER_LINK}>
            YOUR MEMBERS
          </Link>
          <Link href="/elections" className={VOTER_LINK}>
            YOUR BALLOT
          </Link>
          {electionLinks(electionPhase).map(({ href, label }) => (
            <Link key={label} href={href} className={VOTER_LINK}>
              {label}
            </Link>
          ))}
          <Link href="/congress" className={VOTER_LINK}>
            CONGRESS THIS WEEK
          </Link>
        </nav>

        {/* Who pays for this, said once, flat out: it was a tilted rubber
            stamp, a prop that read as a gimmick. Plain mono, the record's
            own face. */}
        <p className="mt-5 max-w-2xl font-mono text-xs uppercase leading-relaxed tracking-[0.1em] text-ink-lo">
          No party money. No PAC money. No ads, no accounts. Open source, self-hosted, run by
          volunteers.
        </p>
      </div>
    </section>
  );
}
