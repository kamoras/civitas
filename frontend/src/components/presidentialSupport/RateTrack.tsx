import type { SupportNumbers } from "@/types/presidentialSupport";
import { expectationSentence, pct } from "@/lib/presidentialSupport";

/**
 * One member's rate on a 0-100% rule: the square is the rate, the boxed span
 * its 95% interval, the cyan tick what the seat's party usually does. Read in
 * a glance: the square off the tick, with the box clear of it, is a record
 * that differs from the seat's norm by more than chance.
 */
export default function RateTrack({ s, label }: { s: SupportNumbers; label: string }) {
  const at = (x: number) => `${Math.min(Math.max(x, 0), 1) * 100}%`;
  return (
    <figure className="m-0">
      <div
        role="img"
        aria-label={`${label}: ${pct(s.rate)}. ${expectationSentence(s)}`}
        className="relative h-10"
      >
        {/* the 0-100 rule, with quarter ticks */}
        <div className="absolute inset-x-0 top-1/2 h-px bg-white/[0.09]" />
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <div
            key={t}
            className="absolute top-[calc(50%-4px)] h-2 w-px bg-white/[0.09]"
            style={{ left: at(t) }}
          />
        ))}
        {s.rateLow != null && s.rateHigh != null && (
          <div
            className="absolute top-[calc(50%-7px)] h-[14px] border border-ink-min"
            style={{ left: at(s.rateLow), width: `calc(${at(s.rateHigh - s.rateLow)} + 1px)` }}
          />
        )}
        {s.expected != null && (
          <div
            className="absolute top-1 bottom-1 w-0.5 -translate-x-1/2 bg-signal-cyan"
            style={{ left: at(s.expected) }}
          />
        )}
        {s.rate != null && (
          <div
            className="absolute top-[calc(50%-5px)] h-2.5 w-2.5 -translate-x-1/2 bg-ink-hi"
            style={{ left: at(s.rate) }}
          />
        )}
      </div>
      <figcaption className="mt-1 flex justify-between font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
        <span>0%</span>
        <span>
          <span className="inline-block h-2 w-2 bg-ink-hi align-middle" /> Votes with the president{" "}
          <span className="ml-2 inline-block h-3 w-0.5 bg-signal-cyan align-middle" /> Seat norm
        </span>
        <span>100%</span>
      </figcaption>
    </figure>
  );
}
