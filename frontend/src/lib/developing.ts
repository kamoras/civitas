/**
 * What a developing Action Center issue was drafted from, in words. Plain
 * module (no "use client") so the server-rendered issue page and the client
 * Action Center read the same wording.
 */

const DEVELOPING_SOURCE: Record<string, string> = {
  senate_roll_call_vote: "a Senate roll-call vote record",
  house_roll_call_vote: "a House roll-call vote record",
  federal_register_significant_rule: "a Federal Register rule",
  election_results: "the state's own election-night count, which is not final",
};

export function developingSource(sourceType: string | null | undefined): string {
  return (sourceType && DEVELOPING_SOURCE[sourceType]) || "a primary source";
}

type IssueKind = { sourceType?: string | null; status?: string | null };

/** Whether an issue's facts are the count: a developing election-results
 * issue (backend live_results/signals.py). Once news coverage confirms
 * one, the Action Center swaps its facts for the outlets' own lines
 * (action_center._promote_developing_issue) while `sourceType` stays
 * "election_results" — so the source type alone would head media quotes
 * "From the count". */
export function factsAreTheCount(issue: IssueKind): boolean {
  return issue.status === "developing" && issue.sourceType === "election_results";
}

/** The heading over an issue's facts. News-derived issues quote their
 * outlets; an election-results issue quotes the count. */
export function factsHeading(issue: IssueKind): string {
  return factsAreTheCount(issue) ? "From the count" : "Media coverage";
}

/** The facts section's anchor and share id, from its heading — so a count
 * issue's link is #from-the-count and its shared image
 * civitas-<id>-from-the-count.png. Every other issue keeps
 * "media-coverage", the id links already out in the world point at. */
export function factsSectionId(issue: IssueKind): string {
  return factsAreTheCount(issue) ? "from-the-count" : "media-coverage";
}

/** Whether a count issue's figures are the state's official count: the
 * backend's own flag (countOfficial). An older backend sends none, and
 * then only its fixed title template (signals._content: "... wins <race>
 * in the official count, ...") says so; anything else reads as not final,
 * the conservative way to be wrong. */
export function countIsOfficial(issue: { title: string; countOfficial?: boolean | null }): boolean {
  if (typeof issue.countOfficial === "boolean") return issue.countOfficial;
  return / in the official count\b/.test(issue.title);
}
