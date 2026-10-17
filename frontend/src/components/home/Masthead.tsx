"use client";

import Link from "next/link";
import { SITE_TAGLINE } from "@/lib/site";
import { stateBallotHref } from "@/lib/elections";
import { useYourState } from "@/hooks/useYourState";
import { ELECTION_MAP_HREF } from "@/lib/electionMap";
import { REDISTRICTING_HREF } from "@/lib/redistricting";
import type { ElectionPhase } from "@/types/election";
import { LONG_LABEL } from "@/lib/controlStyles";

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
  // With a state chosen in the header (this tab only), the two links go
  // straight to that state's members and ballot.
  const { state } = useYourState();
  return (
    <section className="mx-auto max-w-7xl px-4 pt-8 sm:px-6">
      <div className="min-w-0">
        {/* The nameplate, set like a masthead: the name, a heavy rule, the
            line it stands for. It was the pixel "CIVITAS" with two
            off-register colour plates, an effect of its moment. The name
            is hidden from screen readers (the header's link names the site
            right above it); the tagline is read. */}
        <div className="max-w-2xl">
          <p
            aria-hidden="true"
            className="font-display text-xl font-bold uppercase leading-none tracking-[0.22em] text-ink-hi sm:text-2xl"
          >
            Civitas Research
          </p>
          <p className="mt-3 border-t-3 border-ink-hi pt-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-lo">
            {SITE_TAGLINE}
          </p>
        </div>

        <h1 className="mt-6 max-w-2xl font-display text-[26px] font-semibold leading-[1.22] tracking-[-0.015em] text-ink-hi sm:mt-7 sm:text-[32px]">
          A public record of U.S. power: who holds it, who wants it, who pays for it, how they vote
          and what they pass.
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

        {/* The voter's way in, in the first screen. The state is asked once,
            in the header (YourStatePicker); with none chosen, each link
            leads to a page where it is. The researcher's links (methods,
            API, changelog) are in the page's one index. */}
        <nav
          aria-label="For voters"
          className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-xs tracking-[0.1em]"
        >
          <Link
            href={state ? `/politicians?state=${state}` : "/politicians"}
            className={VOTER_LINK}
          >
            YOUR MEMBERS
          </Link>
          <Link href={state ? stateBallotHref(state) : "/elections"} className={VOTER_LINK}>
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
        <p
          className={`mt-5 max-w-2xl font-mono leading-relaxed tracking-[0.1em] text-ink-lo ${LONG_LABEL}`}
        >
          No party money. No PAC money. No ads, no accounts. Open source, self-hosted, run by
          volunteers.
        </p>
      </div>
    </section>
  );
}
