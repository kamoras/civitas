import Link from "next/link";
import type { MapKind, RedistrictingWatch as Watch } from "@/types/redistricting";
import { longDate } from "@/lib/specials";
import { safeHref } from "@/lib/formatting";
import { REDISTRICTING_HREF } from "@/lib/redistricting";

/** How each map in force was set, as a label beside it. */
export const MAP_KIND_LABEL: Record<MapKind, string> = {
  act: "ENACTED BY THE LEGISLATURE",
  ballot_measure: "APPROVED BY VOTERS",
  commission: "ADOPTED BY A COMMISSION",
  court_order: "SET OR HELD BY A COURT",
};

/** The redistricting watch: whether the next general's House maps are
 * confirmed, and every map in force with how it was set (an act, a vote, a
 * commission plan or a court order) and its official source. `compact`
 * lists the states in a line (the /elections lead); the full view lists
 * each with its reason, quoted from the record. */
export default function RedistrictingWatch({
  watch,
  compact = false,
}: {
  watch: Watch | null | undefined;
  compact?: boolean;
}) {
  if (!watch) return null;
  const { nextCycle, confirmed, inForceCycle, maps, lastSettled } = watch;
  const status = confirmed
    ? maps.length
      ? `The ${nextCycle} House maps are confirmed in the record. Each state below changed or kept its lines by an act, a vote, a commission plan or a court order.`
      : `The ${nextCycle} House maps are confirmed: no state redrew.`
    : `The ${nextCycle} House maps aren't confirmed yet. Until a change is recorded, every state votes on the lines in force${inForceCycle ? ` for ${inForceCycle}` : ""}.`;
  return (
    <section
      aria-labelledby="redistricting-watch"
      className="mt-6 border font-sans border-phos/20 bg-surface px-4 pb-4"
    >
      <h2
        id="redistricting-watch"
        className="border-b border-white/[0.07] py-2.5 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        Redistricting watch
      </h2>
      <p className="mt-3 text-sm text-ink-lo">
        {status}
        {lastSettled
          ? ` No decision on a House map has been recorded since ${longDate(lastSettled)}.`
          : ""}
      </p>
      {compact ? (
        maps.length > 0 && (
          <p className="mt-2 text-sm text-ink-lo">
            In force:{" "}
            {maps.map((m, i) => (
              <span key={m.state}>
                {i > 0 && ", "}
                {m.stateName}
                {m.kind === "court_order" ? " (court)" : ""}
              </span>
            ))}
            .{" "}
            <Link
              href={REDISTRICTING_HREF}
              className="inline-flex min-h-6 items-center text-signal-cyan hover:text-phos"
            >
              How each was set →
            </Link>
          </p>
        )
      ) : (
        <ul className="mt-2">
          {maps.map((m) => {
            const url = safeHref(m.source.url ?? "");
            return (
              <li key={m.state} className="border-t border-white/[0.07] py-2">
                <p className="font-mono text-[11px] tracking-[0.12em] text-ink-min">
                  {m.stateName.toUpperCase()}
                  {m.kind ? ` · ${MAP_KIND_LABEL[m.kind]}` : ""}
                  {m.settled ? ` · ${longDate(m.settled).toUpperCase()}` : ""}
                </p>
                <p className="text-sm text-ink-lo">
                  {m.reason}
                  {url && (
                    <>
                      {" "}
                      <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex min-h-6 items-center font-mono text-xs text-signal-cyan hover:text-phos"
                      >
                        [ {m.source.issuedBy ?? "OFFICIAL SOURCE"} ↗ ]
                      </a>
                    </>
                  )}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
