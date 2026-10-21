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
}

/** One dot per member of the party voting Yea or Nay, the breaker's marked. */
export interface DotsFigure {
  type: "dots";
  party: "D" | "R";
  voting: number;
  caption: string;
}

/** Days left in a comment period, and how much of it has gone. */
export interface CountdownFigure {
  type: "countdown";
  opened: string;
  closes: string;
  totalDays: number;
  daysLeft: number;
  elapsedPercent: number;
}

/** One counted figure and what it counts. */
export interface StatFigure {
  type: "stat";
  value: number;
  caption: string;
}

export type Figure = SplitFigure | DotsFigure | CountdownFigure | StatFigure;

/** A record box's kicker colour: an event on the record, a step completed,
 *  a date that closes, or a plain label (components/shared/RecordBox.tsx). */
export type BoxTone = "record" | "done" | "deadline" | "neutral";
