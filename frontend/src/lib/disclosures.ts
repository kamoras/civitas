/** Shared rendering rules for financial-disclosure figures (stock trades and
 * annual-report holdings), so the two scorecard sections can't drift apart. */

export type DisclosureOwner = "self" | "spouse" | "joint" | "dependent";

export const OWNER_LABEL: Record<DisclosureOwner, string> = {
  self: "SELF",
  spouse: "SPOUSE",
  joint: "JOINT",
  dependent: "DEPENDENT",
};

/** A disclosed amount bracket, in full dollars. The forms' open-ended top
 * bracket ("Over $50,000,000") states a floor and no ceiling, so it renders
 * as "$X+" — never as a range, since the stored upper figure is only a
 * placeholder equal to the floor. */
export function formatBracket(low: number, high: number, openEnded: boolean): string {
  const fmt = (n: number) => `$${n.toLocaleString()}`;
  return openEnded ? `${fmt(low)}+` : `${fmt(low)} – ${fmt(high)}`;
}
