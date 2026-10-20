import Link from "next/link";
import { mapHref } from "@/lib/electionMap";
import type { MapSeatLine } from "@/types/electionMap";

/** "USUAL VOTE R+6 · POLLS D+9 TO R+4", the map build's own labels, never
 * re-derived; just the usual vote for a seat with no range, or where
 * `usualOnly` asks for it. */
export function seatFigures(seat: MapSeatLine, usualOnly = false): string {
  const usual = seat.leanLabel ? `Usual vote ${seat.leanLabel}` : "No usual-vote figure";
  return seat.range80Label && !usualOnly ? `${usual} · Polls ${seat.range80Label}` : usual;
}

/** One seat's line, linking its place on the seat map (the polls view when
 * it shows a range, the usual vote otherwise).
 *
 * `usualOnly` for a page that shows a Representation Score: no poll range
 * is ever shown beside one (AGENTS.md, the Senate and House map: "Separate
 * from scoring"), so there the line is the usual vote and the link, as
 * RaceContext does. Where a range is shown, PollRangeSource must name its
 * source and licence beside it. */
export default function SeatOnMap({
  seat,
  label,
  usualOnly = false,
}: {
  seat: MapSeatLine;
  label: string;
  usualOnly?: boolean;
}) {
  const ranged = !!seat.range80Label && !usualOnly;
  return (
    <Link
      href={mapHref(ranged ? "polls" : "usual", seat.chamber, seat.id)}
      className="inline-flex min-h-6 flex-wrap items-center gap-x-2 text-signal-cyan hover:text-phos"
    >
      <span className="text-ink-lo">{label}</span>
      <span>{seatFigures(seat, usualOnly)}</span>
      <span>· Seat map →</span>
    </Link>
  );
}

/** The poll ranges' source and licence, beside any line that shows one:
 * the polls are Wikipedia's tables (CC BY-SA), so the attribution and a
 * link to the licence go wherever a range derived from them does. */
export function PollRangeSource({
  source,
}: {
  source: { attribution: string; license: string; url: string };
}) {
  return (
    <p className="mt-1 text-xs text-ink-min">
      Poll ranges are 80% ranges built on poll tables by {source.attribution} (
      {source.url ? (
        <a
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
          className="underline decoration-white/30 underline-offset-2 hover:text-phos"
        >
          {source.license}
        </a>
      ) : (
        source.license
      )}
      ). Not a race call.
    </p>
  );
}
