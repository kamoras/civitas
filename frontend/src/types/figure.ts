import type { ReadoutMark } from "./readout";

/** A record box's figure (components/shared/RecordFigure.tsx): the
 *  homepage's front page and every page built from RecordBox draw these.
 *  Every number and caption is the backend's; the page only draws them. */

/** Yeas against nays, as a bar. */
export interface SplitFigure {
  type: "split";
  yeas: number;
  nays: number;
  yeaShare: number;
  caption: string;
  /** The bar's two sides' readouts, yea then nay. */
  marks: ReadoutMark[];
}

/** One dot per member of the party voting Yea or Nay, the breaker's marked. */
export interface DotsFigure {
  type: "dots";
  party: "D" | "R";
  voting: number;
  caption: string;
  /** One readout per dot, in drawing order (the breaker last). */
  marks: ReadoutMark[];
}

/** Days left until a date closes: a comment period (with how much of it
 *  has gone, drawn as a bar), or a voter deadline, which has no period to
 *  draw and says what closes in `caption`. */
export interface CountdownFigure {
  type: "countdown";
  opened?: string;
  closes: string;
  totalDays?: number;
  daysLeft: number;
  elapsedPercent?: number;
  /** The period bar's readout, with the bar. */
  marks?: ReadoutMark[];
  /** What the days are left for, after "N days left" ("to register"; ""
   *  for nothing more, where the box's title says what closes). Without
   *  one, a comment period's ("to comment"). */
  caption?: string;
}

/** One counted figure and what it counts. `text`, where given, is how the
 *  backend writes the value ("D+8" for a lean of -8), shown instead of it. */
export interface StatFigure {
  type: "stat";
  value: number;
  caption: string;
  text?: string;
}

export type Figure = SplitFigure | DotsFigure | CountdownFigure | StatFigure;

/** A record box's kicker colour: an event on the record, a step completed,
 *  a date that closes, or a plain label (components/shared/RecordBox.tsx). */
export type BoxTone = "record" | "done" | "deadline" | "neutral";
