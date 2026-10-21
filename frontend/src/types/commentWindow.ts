import type { ReadoutMark } from "./readout";

/** A comment period from publication to its deadline, as of the server's
 *  today in the deadline's zone (backend app/comment_periods.comment_window). */
export interface CommentWindow {
  opened: string;
  closes: string;
  totalDays: number;
  daysLeft: number;
  /** Share of the period already gone, 0 to 100. */
  elapsedPercent: number;
  /** What the period bar's readout says. */
  marks: ReadoutMark[];
}
