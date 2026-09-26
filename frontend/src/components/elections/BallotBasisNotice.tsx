import type { BallotBasis } from "@/types/election";

/**
 * Says what the candidate lists on this page actually are.
 *
 * The page has always shown four quite different things in the same
 * shape: a certified November ballot, primary-derived nominees, a
 * primary ballot, and — when nothing has confirmed anything — every
 * active FEC filer. `candidateSource` distinguished them in the data and
 * nothing said so to the reader.
 *
 * The case that matters is `supersededByPrimary`: filers listed for a
 * contest whose primary has already been decided. Measured on
 * 2026-09-26, eleven states were in that state — Ohio 144 days past its
 * primary, Louisiana 133, New York 95, one New York race showing 25
 * filers for a ballot that holds about two. A voter reading that page
 * was not looking at stale data, they were being told a settled contest
 * was still open. That is the opposite of the platform's purpose, so it
 * gets the loudest treatment on the page rather than a footnote.
 *
 * Deliberately not an apology and not a hedge on everything: a state
 * whose ballot IS certified says nothing at all here, because a notice
 * that appears on every page is one readers learn to skip.
 */
export default function BallotBasisNotice({ basis }: { basis?: BallotBasis | null }) {
  // The frontend and backend are separate containers and are not
  // deployed atomically, so a frontend that lands first sees an API
  // response without this field. Saying nothing is the right failure:
  // the page is exactly as informative as it was before, rather than
  // blank because a notice threw.
  if (!basis) return null;

  if (basis.supersededByPrimary) {
    const days = basis.daysSincePrimary;
    return (
      <div
        role="status"
        className="mb-6 border border-signal-red/50 bg-signal-red/[0.06] p-4"
        data-testid="ballot-superseded"
      >
        <p className="font-mono text-xs tracking-[0.12em] text-signal-red">
          THESE ARE NOT BALLOT POSITIONS
        </p>
        <p className="mt-2 font-display text-sm leading-relaxed text-ink-hi">
          This state&apos;s primary was held
          {typeof days === "number" ? ` ${days} days ago` : ""}, so its November ballot is already
          decided — but we do not have the certified list yet.
        </p>
        <p className="mt-2 font-mono text-xs leading-relaxed text-ink-lo">
          Everyone below filed with the FEC for this seat. Some of them lost their primary and will
          not appear on your ballot. Use your state&apos;s official lookup for the real list.
        </p>
      </div>
    );
  }

  if (basis.basis === "filers") {
    return (
      <p className="mb-6 font-mono text-xs leading-relaxed text-ink-min" data-testid="ballot-filers">
        Candidates below are FEC filers, not confirmed ballot positions — this state&apos;s primary
        has not been held yet, so nobody knows the ballot. Sorted by money raised, which is the only
        published signal of who is running a real campaign.
      </p>
    );
  }

  if (basis.basis === "primary") {
    return (
      <p className="mb-6 font-mono text-xs leading-relaxed text-ink-min" data-testid="ballot-primary">
        Candidates below are on this state&apos;s PRIMARY ballot. Being on it says nothing about
        surviving it.
      </p>
    );
  }

  if (basis.basis === "nominees") {
    return (
      <p
        className="mb-6 font-mono text-xs leading-relaxed text-ink-min"
        data-testid="ballot-nominees"
      >
        Nominees confirmed from primary results. A primary cannot show a Libertarian, Green or
        independent who never ran in one, so this list is real and may be incomplete.
      </p>
    );
  }

  return null;
}
