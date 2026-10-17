import { usableRecord } from "@/lib/ssrPayload";
import { pct } from "@/lib/presidentialSupport";
import type { MemberSupport } from "@/types/presidentialSupport";
import { labelCase } from "@/lib/controlStyles";

const LEAD = "With the president's stated position";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

const VERDICT = {
  more: "more often than expected for the seat",
  less: "less often than expected for the seat",
  within: "about as expected for the seat",
} as const;

/**
 * The member's votes with the president's stated positions in one line at the
 * top of the profile, linking down to the section with the evidence (about
 * eight phone screens below, under the scorecard).
 *
 * Rendered on the server, in the first HTML: fetched in the browser it arrived
 * after the scorecard had painted and pushed it down (layout shift 0.08 on a
 * phone, and the late line became the page's largest paint). Nothing at all
 * when the member has no counted vote or the backend can't answer: a missing
 * line costs a reader nothing, the section below says the same.
 */
export default async function PresidentialSupportLine({ memberId }: { memberId: string }) {
  let data: MemberSupport | null = null;
  try {
    const res = await fetch(
      `${BACKEND}/api/presidential-support/member/${encodeURIComponent(memberId)}`,
      { next: { revalidate: 120 } }
    );
    data = res.ok ? usableRecord<MemberSupport>(await res.json(), "votes", "withPresident") : null;
  } catch {
    data = null;
  }
  if (!data) return null;
  const verdict = data.comparedWithExpected ? VERDICT[data.comparedWithExpected] : null;
  return (
    // Written in sentence case with the count set bright: on a phone it reads
    // as a sentence (it was three lines of 12px caps), and caps from 640px up.
    <p
      className={`mb-3 border border-white/15 bg-surface px-3 py-2 font-mono tracking-[0.08em] text-ink-lo ${labelCase(LEAD)}`}
    >
      {LEAD}:{" "}
      <span className="text-ink-hi">
        {data.withPresident} of {data.votes}
      </span>{" "}
      votes ({pct(data.rate)}){verdict ? `, ${verdict}` : ""}.{" "}
      <a href="#ps-heading" className="inline-block min-h-[24px] text-signal-cyan hover:text-phos">
        The votes →
      </a>
    </p>
  );
}
