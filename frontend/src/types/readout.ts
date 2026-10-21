/** The attribute a shape chart's drawn marks carry: the mark's id. Here,
 *  not in the client ChartReadout module, so a server component that
 *  draws marks gets the string (an export of a "use client" module is a
 *  client reference on the server, and the attribute came out unnamed). */
export const MARK_ATTR = "data-mark";

/** What a chart's readout says about one of its marks (components/shared/
 *  ChartReadout.tsx). Every word and figure is the backend's: the chart
 *  only places it. */
export interface ReadoutMark {
  /** Unique within its chart. */
  id: string;
  /** What the mark is: a member, a side of a vote, a state, a month. */
  label: string;
  /** Party and seat, or a date: a second line under the label. */
  detail?: string | null;
  /** The mark's value as the site prints it, with its unit. */
  shown: string;
  /** Its place among the rest: a rank, a share, a date range, a source. */
  context?: string | null;
  /** The mark's own page, where one exists. */
  href?: string | null;
}
