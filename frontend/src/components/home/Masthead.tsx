"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { billPageHref } from "@/lib/congress";
import { STATE_CODES, STATE_NAMES } from "@/lib/stateCodes";
import { ELECTION_MAP_HREF } from "@/lib/electionMap";
import { REDISTRICTING_HREF } from "@/lib/redistricting";
import type { ElectionPhase } from "@/types/election";

// Listed by name, as a reader looks for their state ("Alabama, Alaska…"),
// not by postal code ("AK, AL…").
const STATES_BY_NAME = [...STATE_CODES].sort((a, b) =>
  (STATE_NAMES[a] ?? a).localeCompare(STATE_NAMES[b] ?? b)
);

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
 * The homepage masthead:overprint wordmark, the statement of what this is,
 * the governance stamp, and the record request slip.
 *
 * Replaces a full-viewport centred hero over a canvas animation. That is
 * marketing-page anatomy — hero, then proof, then a call to action — and it
 * read as a product launch rather than a public-interest project. This is a
 * document header: what the record is, who keeps it, and how to pull
 * something out of it.
 */
export default function Masthead({
  electionPhase = null,
}: {
  electionPhase?: ElectionPhase | null;
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  // A navigation choice only, never kept (AGENTS.md §8): it fills in two
  // links. Before a pick they go to the pages that ask for one.
  const [state, setState] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    // The slip says it takes a bill number: one ("H.R. 7008", "s 4668")
    // opens that bill's page, which full-text search ranked below unrelated
    // documents that merely share the digits. Anything else is a search;
    // /explore reads ?q= on mount and runs it itself.
    router.push(billPageHref(q) ?? `/explore?q=${encodeURIComponent(q)}`);
  };

  return (
    <section className="mx-auto max-w-7xl px-4 pt-8 sm:px-6">
      <div className="grid grid-cols-1 gap-8 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] md:gap-9">
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
            A public record of the people who hold power and the people asking for it, who funds
            them, how they vote, and what they pass.
          </h1>

          <p className="mt-4 max-w-xl font-display text-base leading-relaxed text-ink-lo sm:text-[17px]">
            Scored nightly from federal and state filings. Every number traces back to the document
            it came from. Where a figure is a range, as on the{" "}
            <Link
              href={ELECTION_MAP_HREF}
              className="underline decoration-white/30 underline-offset-4 hover:text-phos"
            >
              election map
            </Link>
            , the page says how it was made.
          </p>

          {/* The voter's way in, in the first screen: who represents me,
              what's on my ballot and what Congress is doing had one
              labelled entry above the fold on a phone between them (the
              state picker for ballots sat two screens down). A state picked
              here only fills in the two links; nothing is asked or kept.
              The researcher's links (methods, API, changelog) are in the
              page's one index, with the source ledger beside them. */}
          <nav
            aria-label="For voters"
            className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-xs tracking-[0.1em]"
          >
            <label className="inline-flex max-w-full flex-wrap items-center gap-2 text-ink-min">
              YOUR STATE:
              <select
                value={state}
                // The code from our own list, never the field's text, into
                // an href.
                onChange={(e) => setState(STATES_BY_NAME.find((c) => c === e.target.value) ?? "")}
                className="min-h-[28px] min-w-0 max-w-full border border-white/20 bg-surface-base px-1 font-mono text-xs tracking-normal text-ink"
              >
                <option value="">Pick one</option>
                {STATES_BY_NAME.map((code) => (
                  <option key={code} value={code}>
                    {STATE_NAMES[code] ?? code}
                  </option>
                ))}
              </select>
            </label>
            <Link
              href={state ? `/politicians?state=${state}` : "/politicians"}
              className={VOTER_LINK}
            >
              YOUR MEMBERS
            </Link>
            <Link href={state ? `/elections/states/${state}` : "/elections"} className={VOTER_LINK}>
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
        </div>

        <div className="min-w-0">
          <form
            onSubmit={submit}
            className="border border-ink-min/45"
            aria-labelledby="request-a-record"
          >
            <h2
              id="request-a-record"
              className="border-b border-ink-min/45 bg-white/[0.04] px-3 py-1.5 font-mono text-xs tracking-[0.16em] text-ink-lo"
            >
              REQUEST A RECORD
            </h2>
            <div className="px-3 py-3">
              <label
                htmlFor="record-request"
                className="mb-2 block font-mono text-xs tracking-[0.1em] text-ink-min"
              >
                TOPIC · BILL NO. · DOCUMENT
              </label>
              <input
                id="record-request"
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. prescription drug pricing"
                className="w-full border-0 border-b border-white/20 bg-transparent pb-1 font-mono text-sm text-ink-hi placeholder:text-ink-min focus:border-phos focus:outline-none"
              />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <button
                  type="submit"
                  className="font-mono text-sm tracking-[0.12em] text-phos-mid hover:text-phos"
                >
                  [ PULL THE FILE ]
                </button>
                {/* No count here on purpose. This read "535 on file", a
                    literal, three lines under "Every number traces back to the document it came from" —
                    and wrong twice over: it excludes the presidents and
                    justices the directory also holds, and the House is only
                    momentarily at 435 between vacancies. The real number needs
                    /api/politicians, which has no pagination and returns the
                    entire roster, which is not a fetch a label is worth. The
                    directory prints its own count on arrival. */}
                <Link
                  href="/politicians"
                  className="font-mono text-xs tracking-[0.06em] text-ink-min hover:text-ink-lo"
                >
                  Everyone on file →
                </Link>
              </div>
            </div>
          </form>

          {/* Governance disclosure, asserted rather than murmured in a
              footer. Deliberately not a charity claim — Civitas isn't a
              registered nonprofit, or a registered organization of any
              kind; it's an open source project maintained for free (2026-08
              correction: earlier copy called it "non-profit," which implies
              a registered entity that doesn't exist).

              Below the request slip, not above it. The stamp is the most
              saturated thing on the page by some margin — full-strength
              magenta, rotated, against a column of ink and phosphor — so
              placing it first made boilerplate the first thing the eye landed
              on, ahead of both the record index and the only control on the
              page. Reading order now runs control, then disclosure. */}
          <p className="stamp mt-7 text-xs leading-[1.75]">
            Open source · free to use
            <br />
            No party, candidate
            <br />
            or PAC money
            <br />
            <span className="text-signal-red">AGPL-3.0 · SELF-HOSTED</span>
          </p>
        </div>
      </div>
    </section>
  );
}
