"use client";

import Link from "next/link";
import { useState } from "react";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import RateTrack from "@/components/presidentialSupport/RateTrack";
import VoteList, { voteAnchor } from "@/components/presidentialSupport/VoteList";
import MethodNotes from "@/components/presidentialSupport/MethodNotes";
import { fetchMemberPresidentialSupport } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import {
  expectationSentence,
  leanLabel,
  presidentSupportHref,
  recordSentence,
} from "@/lib/presidentialSupport";
import type { MemberSupport } from "@/types/presidentialSupport";

/**
 * A member's votes with the president's stated positions, on the profile.
 *
 * Casual layer: one sentence, the rate against the seat's norm, and a tally
 * with a square per counted vote (filled: on the president's side). Expert
 * layer, one click down: every vote with its Statement of Administration
 * Policy, the method, and the data. Kept in its own file so the profile
 * page carries a single line for it.
 */
export default function PresidentialSupportSection({
  memberId,
  name,
}: {
  memberId: string;
  name: string;
}) {
  const { data, loading, error } = useAsyncData<MemberSupport | null>(
    `presidential-support:${memberId}`,
    () => fetchMemberPresidentialSupport(memberId)
  );
  const [open, setOpen] = useState(false);
  if (loading || error || !data) return null;

  // Oldest first, so the tally reads left to right through the Congress.
  const counted = data.rollCalls.filter((v) => v.withPresident != null).reverse();
  const jsonHref = `/api/presidential-support/member/${encodeURIComponent(memberId)}`;

  return (
    <section aria-labelledby="ps-heading" className="mb-6" data-share-section="with-the-president">
      <TerminalTitlebar title="With the president" />
      <div className="border border-t-0 border-white/[0.07] bg-surface-base p-4">
        <h2 id="ps-heading" className="font-display text-lg font-semibold text-ink-hi">
          How often {name} votes with the president
        </h2>
        <p className="mt-2 max-w-2xl font-sans text-base leading-relaxed text-ink">
          {recordSentence(name, data)} {expectationSentence(data)}
        </p>

        <div className="mt-4 max-w-2xl">
          <RateTrack s={data} label={`${name}, votes with the president's stated position`} />
        </div>

        <div className="mt-4">
          <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
            Each square is one counted vote, oldest first · filled: on the president&apos;s side
          </p>
          <div aria-hidden="true" className="mt-2 flex max-w-2xl flex-wrap gap-[3px]">
            {counted.map((v) => (
              <a
                key={voteAnchor(v)}
                href={`#${voteAnchor(v)}`}
                tabIndex={-1}
                onClick={() => setOpen(true)}
                title={`${v.date} · ${v.billId ?? ""} · ${v.withPresident ? "with" : "against"}`}
                className={`block h-2.5 w-2.5 border ${
                  v.withPresident ? "border-ink-hi bg-ink-hi" : "border-ink-min bg-transparent"
                } hover:border-phos`}
              />
            ))}
          </div>
        </div>

        <details
          className="mt-5 border-t border-white/[0.07] pt-3"
          open={open}
          onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
        >
          <summary className="cursor-pointer font-mono text-xs uppercase tracking-[0.12em] text-ink-lo hover:text-phos">
            [The {data.votes} votes, the method and the data]
          </summary>
          <div className="mt-3 space-y-5">
            <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
              n = {data.votes} · rate {data.rate} · 95% range {data.rateLow} to {data.rateHigh} ·
              expected {data.expected ?? "n/a"} · seat {leanLabel(data.seatLean)}
            </p>
            <MethodNotes method={data.method} jsonHref={jsonHref} />
            <VoteList votes={data.rollCalls} member={name} />
          </div>
        </details>

        <Link
          href={presidentSupportHref(data.chamber)}
          className="mt-4 inline-block font-mono text-xs uppercase tracking-[0.12em] text-signal-cyan hover:text-phos"
        >
          [Compare the whole {data.chamber === "senate" ? "Senate" : "House"}] →
        </Link>
      </div>
    </section>
  );
}

/**
 * The same finding in one line, for the top of the profile: the full section
 * sits below the scorecard, about eight phone screens down, so a reader gets
 * the answer here and one tap takes them to the evidence. Same request as the
 * section (cachedFetch shares it).
 */
export function PresidentialSupportLine({ memberId }: { memberId: string }) {
  const { data } = useAsyncData<MemberSupport | null>(`presidential-support:${memberId}`, () =>
    fetchMemberPresidentialSupport(memberId)
  );
  if (!data) return null;
  const verdict =
    data.comparedWithExpected === "more"
      ? "more often than expected for the seat"
      : data.comparedWithExpected === "less"
        ? "less often than expected for the seat"
        : data.comparedWithExpected === "within"
          ? "about as expected for the seat"
          : null;
  return (
    <p className="mb-3 border border-white/15 bg-surface px-3 py-2 font-mono text-xs tracking-[0.08em] text-ink-lo">
      WITH THE PRESIDENT&apos;S STATED POSITION · {data.withPresident} OF {data.votes} VOTES (
      {Math.round((data.rate ?? 0) * 100)}%){verdict ? ` · ${verdict.toUpperCase()}` : ""} ·{" "}
      <a href="#ps-heading" className="inline-block min-h-[24px] text-signal-cyan hover:text-phos">
        THE VOTES →
      </a>
    </p>
  );
}
