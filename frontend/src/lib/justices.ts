import { GITHUB_REPO_URL } from "@/lib/site";

/** Why no justice is scored (justice v3), shown wherever a score would be. */
export const NOT_SCORED_REASON =
  "No method yet separates loyalty to the appointing president from career timing for an individual justice.";

export const JUSTICE_RESEARCH_URL = `${GITHUB_REPO_URL}/blob/main/docs/research/justice-scores.md`;

/** The Court-level finding, stated as fact: the appointer effect pooled over
 *  every justice since 1937, less what a placebo window at the same point in
 *  a career shows, with a bootstrap interval over justices. Printed by
 *  section 8 of backend/scripts/research_justice_loyalty.py (Supreme Court
 *  Database 2026 Release 01); update it with docs/research/justice-scores.md
 *  when that is rerun. */
export const COURT_FINDING =
  "Across every justice since 1937, justices side with the federal government about 2.5 points more often while their appointing president is in office, once career timing is set aside (95% interval −0.7 to +5.6): not distinguishable from no effect.";
