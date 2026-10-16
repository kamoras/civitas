import Link from "next/link";
import { mapHref } from "@/lib/electionMap";
import type { MapSeatLine } from "@/types/electionMap";

/** "USUAL VOTE R+6 · POLLS D+9 TO R+4", the map build's own labels, never
 * re-derived; just the usual vote for a seat with no range. */
export function seatFigures(seat: MapSeatLine): string {
  const usual = seat.leanLabel ? `USUAL VOTE ${seat.leanLabel}` : "NO USUAL-VOTE FIGURE";
  return seat.range80Label ? `${usual} · POLLS ${seat.range80Label.toUpperCase()}` : usual;
}

/** One seat's line, linking its place on the seat map (the polls view when
 * it has a range, the usual vote otherwise). */
export default function SeatOnMap({ seat, label }: { seat: MapSeatLine; label: string }) {
  return (
    <Link
      href={mapHref(seat.range80Label ? "polls" : "usual", seat.chamber, seat.id)}
      className="inline-flex min-h-6 flex-wrap items-center gap-x-2 text-signal-cyan hover:text-phos"
    >
      <span className="text-ink-lo">{label}</span>
      <span>{seatFigures(seat)}</span>
      <span>· SEAT MAP →</span>
    </Link>
  );
}

/** The poll ranges' source and licence, beside any line that shows one. */
export function PollRangeSource({ source }: { source: { attribution: string; license: string } }) {
  return (
    <p className="mt-1 text-xs text-ink-min">
      Poll ranges are 80% ranges built on poll tables by {source.attribution} ({source.license}).
      Not a race call.
    </p>
  );
}
