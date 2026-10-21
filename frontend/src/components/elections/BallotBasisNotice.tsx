import type { BallotBasis } from "@/types/election";
import NoteLine from "./NoteLine";

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
 * gets the loudest treatment on the page rather than a footnote: the one
 * note in red, above the contests (each note is one line, its full text
 * behind "More": NoteLine).
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
      <div role="status">
        <NoteLine
          alert
          testId="ballot-superseded"
          lead="These are not ballot positions: the primary is decided, the certified list isn't here yet."
        >
          <p>
            This state&apos;s primary was held
            {typeof days === "number" ? ` ${days} days ago` : ""}, so its November ballot is already
            decided, but we do not have the certified list yet.
          </p>
          <p className="mt-1">
            Everyone below filed with the FEC for this seat. Some of them lost their primary and
            will not appear on your ballot. Use your state&apos;s official lookup for the real list.
          </p>
        </NoteLine>
      </div>
    );
  }

  if (basis.basis === "filers") {
    return (
      <NoteLine
        testId="ballot-filers"
        lead="Everyone below is registered with the FEC, not on the ballot."
      >
        This state&apos;s primary has not been held yet, and the state certifies its own ballot
        after it. Sorted by money raised, which is the only published signal of who is running a
        real campaign.
      </NoteLine>
    );
  }

  if (basis.basis === "primary") {
    return (
      <NoteLine testId="ballot-primary" lead="Candidates below are on this state's primary ballot.">
        Being on it says nothing about surviving it.
      </NoteLine>
    );
  }

  if (basis.basis === "nominees") {
    return (
      <NoteLine
        testId="ballot-nominees"
        lead="Nominees confirmed from primary results: real, and possibly incomplete."
      >
        A primary cannot show a nominee who never ran in one (one a party convention chose, or a
        Libertarian, Green or independent candidate), so this list is real and may be incomplete.
      </NoteLine>
    );
  }

  return null;
}
