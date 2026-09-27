/** Shared rendering rules for financial-disclosure figures (stock trades and
 * annual-report holdings), so the two scorecard sections can't drift apart. */

/** "unknown": an annual report's owner value the parser didn't recognize —
 * shown as such, never assumed to be the member's. */
export type DisclosureOwner = "self" | "spouse" | "joint" | "dependent" | "unknown";

export const OWNER_LABEL: Record<DisclosureOwner, string> = {
  self: "SELF",
  spouse: "SPOUSE",
  joint: "JOINT",
  dependent: "DEPENDENT",
  unknown: "OWNER NOT STATED",
};

/** A disclosed amount bracket, or a sum of them. The forms' open-ended top
 * bracket ("Over $50,000,000") states a floor and no ceiling, so it renders
 * as "$X+" — never as a range, since the stored upper figure is only a
 * placeholder equal to the floor. */
export function formatBracket(
  low: number,
  high: number,
  openEnded: boolean,
  /** How to print one figure; full dollars by default, or a compact form
   * ("$1.2M") for sums. The open-ended rule is the same either way. */
  fmt: (n: number) => string = (n) => `$${n.toLocaleString()}`
): string {
  return openEnded ? `${fmt(low)}+` : `${fmt(low)} – ${fmt(high)}`;
}

/** When a report's holdings were held, for sentences like "None at year
 * end": an annual report describes the year end, a Senate new-filer report
 * the date it states. A report whose date isn't known (a paper filing, or a
 * title that states none) is described without claiming one. */
export function asOfPhrase(asOfDate: string | null): string {
  if (!asOfDate) return "as of the report's date";
  return asOfDate.endsWith("-12-31") ? "at year end" : `on ${asOfDate}`;
}
