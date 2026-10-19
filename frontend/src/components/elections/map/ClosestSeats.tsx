import type { MapSeat, RangedSeat } from "@/types/electionMap";
import { seatName, type MapView } from "@/lib/electionMap";

/** How many seats the list shows; the table below the map has every one. */
const SHOWN = 8;

/** The seats a phone reader most likely came for, in the order the current
 *  view reads them: nearest even in the usual vote; nearest even at the
 *  middle of the poll range (the order the table's range column sorts by);
 *  or where polls disagree most with the usual vote. Every figure is the
 *  backend's label; this only orders and cuts the list. */
export function closestSeats(seats: MapSeat[], view: MapView): MapSeat[] {
  const ranged = seats.filter((s): s is RangedSeat => s.status === "range");
  if (view === "usual")
    return seats
      .filter((s) => s.lean != null)
      .sort((a, b) => Math.abs(a.lean!) - Math.abs(b.lean!))
      .slice(0, SHOWN);
  if (view === "polls")
    return ranged.sort((a, b) => Math.abs(a.mean) - Math.abs(b.mean)).slice(0, SHOWN);
  return ranged
    .filter((s) => s.difference.strength > 0)
    .sort((a, b) => b.difference.strength - a.difference.strength)
    .slice(0, SHOWN);
}

const HEADING: Record<MapView, string> = {
  usual: "nearest even in the usual vote",
  polls: "nearest even in the middle of the poll range",
  diff: "where polls disagree most with the usual vote",
};

/**
 * The seat map's phone form (mobile spec P6): the closest seats as 44px rows
 * under the map, each opening the same sheet a tile does. On a phone the
 * list is the way in and the map the picture: a tile is 28px with a 9px
 * label, a row is the whole width and says its lean in words (1.1.1, 1.4.1).
 */
export default function ClosestSeats({
  seats,
  view,
  onSelect,
}: {
  seats: MapSeat[];
  view: MapView;
  onSelect: (id: string) => void;
}) {
  const rows = closestSeats(seats, view);
  if (!rows.length) return null;
  return (
    <section aria-labelledby="closest-seats" className="mt-4">
      <h3 id="closest-seats" className="font-mono text-xs uppercase tracking-[0.14em] text-ink-hi">
        Closest seats
      </h3>
      <p className="mt-1 text-sm text-ink-lo">
        The {rows.length} {rows.length === 1 ? "seat" : "seats"} {HEADING[view]}.
      </p>
      <ul className="mt-2 border-t border-white/[0.09]">
        {rows.map((s) => (
          <li key={s.id} className="border-b border-white/[0.09]">
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              className="flex min-h-11 w-full items-center justify-between gap-3 text-left hover:text-phos"
            >
              <span className="min-w-0 text-[15px] text-ink-hi">{seatName(s)}</span>
              <span className="flex shrink-0 items-center gap-2 font-mono text-sm text-ink-lo">
                {view === "usual" || s.status !== "range"
                  ? (s.leanLabel ?? "")
                  : view === "polls"
                    ? s.range80Label
                    : `polls lean ${s.difference.direction === "D" ? "toward D" : "toward R"}${s.difference.strength > 1 ? ", strongly" : ""}`}
                <span aria-hidden="true">›</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
