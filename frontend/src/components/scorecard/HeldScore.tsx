import type { HeldScore } from "@/types/senator";
import { SCORE_TERMS, type ScoreKey } from "@/lib/scoreTerms";
import { asLabel } from "@/lib/formatting";

/** The sentence a held score carries: whose run it is and why the latest
 *  run's isn't shown. Every figure is the backend's (score_keeping.held). */
export function heldScoreText(held: HeldScore): string {
  const run = held.scoredOn ? `the ${held.scoredOn} run` : "an earlier run";
  const version = held.algorithmVersion ? ` (method ${held.algorithmVersion})` : "";
  const why = held.source
    ? `the ${held.failedOn} run couldn't read ${held.source}`
    : `the ${held.failedOn} run couldn't score this member`;
  return `Score from ${run}${version}: ${why}, so other members' scores may be newer.`;
}

/** Short mark beside a held score in a list (leaderboard, compare), the
 *  full sentence for screen readers and on hover. */
export function HeldScoreTag({ held }: { held?: HeldScore | null }) {
  if (!held) return null;
  const text = heldScoreText(held);
  return (
    <span
      title={text}
      className="ml-2 inline-block whitespace-nowrap border border-signal-amber/40 px-1 font-mono text-xs text-signal-amber"
    >
      {/* The run's date where a row has room for it; a phone's card keeps
          the member's name instead. */}
      <span aria-hidden="true">
        Held
        {held.scoredOn && <span className="hidden md:inline"> · {held.scoredOn.slice(5)}</span>}
      </span>
      <span className="sr-only">{text}</span>
    </span>
  );
}

/** The same, as a line under a profile's score. */
export function HeldScoreNote({ held }: { held?: HeldScore | null }) {
  if (!held) return null;
  return (
    <p className="border-l-2 border-signal-amber/60 pl-3 text-sm leading-relaxed text-ink-lo">
      <span className="font-mono text-xs text-signal-amber">Held</span>
      {""}
      {heldScoreText(held)}
    </p>
  );
}

/** Under a profile's score, only when a weighted part has little data
 *  behind it (the backend's thinData, from the stored confidence grades). */
export function ThinDataNote({ dims }: { dims?: string[] }) {
  if (!dims || dims.length === 0) return null;
  const which = dims.map((d) => SCORE_TERMS[d as ScoreKey]?.label ?? asLabel(d)).join(", ");
  return (
    <p className="border-l-2 border-signal-amber/60 pl-3 text-sm leading-relaxed text-ink-lo">
      <span className="font-mono text-xs text-signal-amber">Thin data</span> Little data is behind{" "}
      {which}, so {dims.length === 1 ? "that part is" : "those parts are"} mostly pulled toward a
      middle value, not measured.
    </p>
  );
}
