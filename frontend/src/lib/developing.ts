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

/** The heading over an issue's facts. News-derived issues quote their
 * outlets; an election-results issue quotes the count. */
export function factsHeading(sourceType: string | null | undefined): string {
  return sourceType === "election_results" ? "From the count" : "Media coverage";
}
