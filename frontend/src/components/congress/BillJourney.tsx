import type { BillJourney as Journey, BillJourneyStop, BillPassage } from "@/types/congress";
import { calendarDate } from "@/lib/congress";

/** What a screen reader hears after a stop's name; on screen the mark and
 * the dimming say it. */
const STATUS_TEXT: Record<BillJourneyStop["status"], string> = {
  done: "",
  current: " (where it is now)",
  skipped: " (skipped)",
  failed: " (failed)",
  ahead: " (not reached)",
};

function Mark({ status }: { status: BillJourneyStop["status"] }) {
  const shape =
    status === "done"
      ? "rounded-full bg-signal-cyan"
      : status === "current"
        ? "rounded-full bg-signal-cyan ring-4 ring-signal-cyan/30"
        : status === "failed"
          ? "rotate-45 bg-signal-red"
          : status === "skipped"
            ? "rounded-full border-2 border-dashed border-ink-min"
            : "rounded-full border-2 border-ink-min";
  return <span className={`relative z-10 inline-block h-3 w-3 shrink-0 ${shape}`} />;
}

/**
 * How a bill becomes law, through this bill: each stop its type passes
 * (introduced, committee, each chamber's floor, the President, law), dated
 * from its official actions, with the time between stops, and then how each
 * chamber passed it. Built and worded by the backend (bill_journey.py); the
 * list is its own text equivalent, read in order.
 */
export default function BillJourney({
  journey,
  passage = [],
}: {
  journey: Journey;
  passage?: BillPassage[];
}) {
  const methods = new Map(
    passage.filter((p) => p.methodTerm && p.methodMeaning).map((p) => [p.methodTerm, p])
  );
  return (
    <div className="flex flex-col gap-4">
      <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">Its path</p>
      <ol className="flex flex-col lg:flex-row">
        {journey.stops.map((s, i) => {
          const reached = s.status !== "ahead";
          const last = i === journey.stops.length - 1;
          return (
            <li
              key={s.key}
              aria-current={s.status === "current" ? "step" : undefined}
              className="relative flex gap-3 pb-4 lg:flex-1 lg:flex-col lg:gap-2 lg:pb-0 lg:pr-2"
            >
              {/* The track: down the left on a phone, across on a wide
                  screen, solid up to the furthest stop reached. */}
              {!last && (
                <span
                  aria-hidden="true"
                  className={`absolute left-[5px] top-3 h-full w-px lg:left-3 lg:top-[5px] lg:h-px lg:w-full ${
                    journey.stops[i + 1].status === "ahead" ? "bg-white/15" : "bg-signal-cyan/70"
                  }`}
                />
              )}
              <span aria-hidden="true" className="pt-1 lg:pt-0">
                <Mark status={s.status} />
              </span>
              <span className={`flex min-w-0 flex-col ${reached ? "" : "opacity-70"}`}>
                <span className={`text-sm font-bold ${reached ? "text-ink-hi" : "text-ink-lo"}`}>
                  {s.label}
                  <span className="sr-only">{STATUS_TEXT[s.status]}</span>
                </span>
                {s.status === "current" && (
                  <span
                    aria-hidden="true"
                    className="w-fit border border-signal-cyan/50 px-1 font-mono text-[11px] uppercase tracking-[0.12em] text-signal-cyan"
                  >
                    Now
                  </span>
                )}
                {s.detail && (
                  <span className="text-xs text-ink-lo">
                    {s.detail}
                    {s.date && s.key === "committee" && s.status === "current"
                      ? ` ${calendarDate(s.date)}`
                      : ""}
                  </span>
                )}
                {s.date && !(s.key === "committee" && s.status === "current") && (
                  <span className="font-mono text-xs tabular-nums text-ink">
                    {calendarDate(s.date)}
                  </span>
                )}
                {s.after && <span className="text-xs text-ink-min">{s.after}</span>}
                {!reached && <span className="text-xs text-ink-min">Not yet</span>}
              </span>
            </li>
          );
        })}
      </ol>
      {passage.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
            How it passed
          </p>
          <ul className="flex flex-col gap-1.5 text-sm leading-relaxed text-ink">
            {passage.map((p) => (
              <li key={`${p.chamber}-${p.date}-${p.kind}`}>{p.sentence}</li>
            ))}
          </ul>
          {methods.size > 0 && (
            <dl className="flex flex-col gap-1 text-sm leading-relaxed text-ink-lo">
              {[...methods.values()].map((p) => (
                <div key={p.methodTerm}>
                  <dt className="inline font-bold text-ink">{p.methodTerm}:</dt>{" "}
                  <dd className="inline">{p.methodMeaning}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}
    </div>
  );
}
