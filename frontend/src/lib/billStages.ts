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
 * the design tokens, all of which are contrast-checked against `#0D0208`.
 *
 * That now covers the non-text accents too. Leaving the bar and the row rule
 * on the backend's hex meant one stage rendered in two different colours in
 * the same row of the same page — IN_COMMITTEE's label came out `dem-blue`
 * (#6699FF) beside a bar drawn in the backend's #3B82F6, and PASSED_CHAMBER's
 * `ind-purple` (#AC56FF) beside #8B5CF6. Near-misses like that read as a
 * rendering fault rather than a palette, and they are what made the pipeline
 * the one row on the site that looks like a stock dashboard.
 *
 * `/config`'s `color` is consequently no longer read for stage colour
 * anywhere in the UI. A stage this build has not heard of falls back to
 * UNKNOWN_STAGE, which is neutral and legible rather than invisible.
 */

export interface BillStageStyle {
  /** Token class for the badge label. */
  text: string;
  /** Token class for the badge border. */
  border: string;
  /** Token class for the badge fill. */
  bg: string;
  /** Token class for a solid fill — the pipeline funnel's bar. */
  bar: string;
  /** Token class for the left rule on a bill row. */
  rule: string;
}

const STAGE_STYLES: Record<string, BillStageStyle> = {
  INTRODUCED: {
    text: "text-ink-lo",
    border: "border-white/15",
    bg: "bg-white/[0.04]",
    bar: "bg-ink-min",
    rule: "border-l-ink-min",
  },
  REFERRED: {
    text: "text-signal-cyan",
    border: "border-signal-cyan/30",
    bg: "bg-signal-cyan/10",
    bar: "bg-signal-cyan",
    rule: "border-l-signal-cyan",
  },
  IN_COMMITTEE: {
    text: "text-dem-blue",
    border: "border-dem-blue/30",
    bg: "bg-dem-blue/10",
    bar: "bg-dem-blue",
    rule: "border-l-dem-blue",
  },
  PASSED_CHAMBER: {
    text: "text-ind-purple",
    border: "border-ind-purple/30",
    bg: "bg-ind-purple/10",
    bar: "bg-ind-purple",
    rule: "border-l-ind-purple",
  },
  IN_OTHER_CHAMBER: {
    text: "text-signal-amber",
    border: "border-signal-amber/30",
    bg: "bg-signal-amber/10",
    bar: "bg-signal-amber",
    rule: "border-l-signal-amber",
  },
  TO_PRESIDENT: {
    text: "text-signal-magenta",
    border: "border-signal-magenta/30",
    bg: "bg-signal-magenta/10",
    bar: "bg-signal-magenta",
    rule: "border-l-signal-magenta",
  },
  ENACTED: {
    text: "text-phos",
    border: "border-phos/30",
    bg: "bg-phos/10",
    bar: "bg-phos",
    rule: "border-l-phos",
  },
  VETOED: {
    text: "text-signal-red",
    border: "border-signal-red/30",
    bg: "bg-signal-red/10",
    bar: "bg-signal-red",
    rule: "border-l-signal-red",
  },
};

/** Neutral, and legible, for a stage this build has never heard of. */
const UNKNOWN_STAGE: BillStageStyle = {
  text: "text-ink-lo",
  border: "border-white/15",
  bg: "bg-white/[0.04]",
  bar: "bg-ink-min",
  rule: "border-l-ink-min",
};

export function billStageStyle(stageCode: string): BillStageStyle {
  return STAGE_STYLES[stageCode] ?? UNKNOWN_STAGE;
}

/** Every stage code this build styles deliberately. */
export const STYLED_STAGE_CODES = Object.keys(STAGE_STYLES);
