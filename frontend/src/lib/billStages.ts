/**
 * How a bill stage is coloured.
 *
 * The backend's `/config` supplies each stage's display colour as a raw hex,
 * and the frontend used to paint the badge *text* with it directly. That put
 * an arbitrary value from another service inside the contrast budget: the
 * default violet for PASSED_CHAMBER measures 4.45:1 on the panel background,
 * under the 4.5:1 floor, and nothing in either codebase would have caught a
 * new stage landing at 3:1.
 *
 * Same lesson as `partyStyles` and `getScoreColor`: the backend names the
 * *thing*, the frontend decides what it looks like. Every value here is one of
 * the design tokens, all of which are contrast-checked against `#0D0208`. The
 * backend's hex is still used for the non-text accents — the row's left rule
 * and the badge fill — where the floor does not apply.
 */

export interface BillStageStyle {
  /** Token class for the badge label. */
  text: string;
  /** Token class for the badge border. */
  border: string;
  /** Token class for the badge fill. */
  bg: string;
}

const STAGE_STYLES: Record<string, BillStageStyle> = {
  INTRODUCED: { text: "text-ink-lo", border: "border-white/15", bg: "bg-white/[0.04]" },
  REFERRED: {
    text: "text-signal-cyan",
    border: "border-signal-cyan/30",
    bg: "bg-signal-cyan/10",
  },
  IN_COMMITTEE: { text: "text-dem-blue", border: "border-dem-blue/30", bg: "bg-dem-blue/10" },
  PASSED_CHAMBER: {
    text: "text-ind-purple",
    border: "border-ind-purple/30",
    bg: "bg-ind-purple/10",
  },
  IN_OTHER_CHAMBER: {
    text: "text-signal-amber",
    border: "border-signal-amber/30",
    bg: "bg-signal-amber/10",
  },
  TO_PRESIDENT: {
    text: "text-signal-magenta",
    border: "border-signal-magenta/30",
    bg: "bg-signal-magenta/10",
  },
  ENACTED: { text: "text-phos", border: "border-phos/30", bg: "bg-phos/10" },
  VETOED: { text: "text-signal-red", border: "border-signal-red/30", bg: "bg-signal-red/10" },
};

/** Neutral, and legible, for a stage this build has never heard of. */
const UNKNOWN_STAGE: BillStageStyle = {
  text: "text-ink-lo",
  border: "border-white/15",
  bg: "bg-white/[0.04]",
};

export function billStageStyle(stageCode: string): BillStageStyle {
  return STAGE_STYLES[stageCode] ?? UNKNOWN_STAGE;
}

/** Every stage code this build styles deliberately. */
export const STYLED_STAGE_CODES = Object.keys(STAGE_STYLES);
