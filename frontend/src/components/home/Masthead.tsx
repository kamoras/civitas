"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

/**
 * The homepage masthead: overprint wordmark, the statement of what this is,
 * the governance stamp, and the record request slip.
 *
 * Replaces a full-viewport centred hero over a canvas animation. That is
 * marketing-page anatomy — hero, then proof, then a call to action — and it
 * read as a product launch rather than a public-interest project. This is a
 * document header: what the record is, who keeps it, and how to pull
 * something out of it.
 */
export default function Masthead() {
  const router = useRouter();
  const [query, setQuery] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    // /explore reads ?q= on mount and runs the search itself.
    router.push(`/explore?q=${encodeURIComponent(q)}`);
  };

  return (
    <section className="mx-auto max-w-7xl px-4 pt-8 sm:px-6">
      <div className="grid grid-cols-1 gap-8 md:grid-cols-12 md:gap-9">
        <div className="md:col-span-8">
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
            className="overprint font-pixel text-2xl leading-none text-ink-hi sm:text-3xl"
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
              way. */}
          <h1 className="mt-6 max-w-2xl font-display text-[26px] font-semibold leading-[1.22] tracking-[-0.015em] text-ink-hi sm:mt-7 sm:text-[32px]">
            A public record of who funds every member of Congress, how they vote, and what they
            pass.
          </h1>

          <p className="mt-4 max-w-xl font-display text-base leading-relaxed text-ink-lo sm:text-[17px]">
            Scored nightly from federal filings. Every number traces back to the document it came
            from. Nothing here is an estimate.
          </p>
        </div>

        <div className="md:col-span-4">
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
                    literal, three lines under "Nothing here is an estimate" —
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
              footer. Deliberately not a charity claim — Civitas is a
              non-profit public-interest project, not a registered 501(c)(3).

              Below the request slip, not above it. The stamp is the most
              saturated thing on the page by some margin — full-strength
              magenta, rotated, against a column of ink and phosphor — so
              placing it first made boilerplate the first thing the eye landed
              on, ahead of both the record index and the only control on the
              page. Reading order now runs control, then disclosure. */}
          <p className="stamp mt-7 text-xs leading-[1.75]">
            Non-profit · free to use
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
