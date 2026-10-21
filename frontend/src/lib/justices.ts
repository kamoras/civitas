import { GITHUB_REPO_URL } from "@/lib/site";
import type { AppointerFinding } from "@/types/court";

/** Why no justice is scored (justice v3), shown wherever a score would be. */
export const NOT_SCORED_REASON =
  "No method yet separates loyalty to the appointing president from career timing for an individual justice.";

export const JUSTICE_RESEARCH_URL = `${GITHUB_REPO_URL}/blob/main/docs/research/justice-scores.md`;

/** A share as signed points, "+6.3" / "−4.3" (a true minus sign): the
 *  estimate and both ends of its interval on one scale, positive meaning
 *  more often under the appointing president. */
export function signedPoints(share: number): string {
  const v = share * 100;
  return `${v >= 0 ? "+" : "\u2212"}${Math.abs(v).toFixed(1)}`;
}

/** The Court-level finding, stated as fact: the appointer effect pooled over
 *  every justice, less what a placebo window at the same point in a career
 *  shows, with a bootstrap interval over justices. The figures are the
 *  backend's (app/data/court_finding.json, written by
 *  scripts/research_justice_loyalty.py --write-finding), never typed here;
 *  null when they can't be read, and the page then says nothing. */
export function courtFinding(f: AppointerFinding | null | undefined): string | null {
  if (!f) return null;
  const points = Math.abs(f.estimate * 100).toFixed(1);
  const direction = f.estimate >= 0 ? "more" : "less";
  const reading = f.excludesZero
    ? "distinguishable from no effect."
    : "not distinguishable from no effect.";
  return (
    `Across every justice since ${f.firstTerm}, justices side with the federal government about ` +
    `${points} points ${direction} often while their appointing president is in office, once ` +
    `career timing is set aside (95% interval ${signedPoints(f.low)} to ${signedPoints(f.high)}): ` +
    reading
  );
}
