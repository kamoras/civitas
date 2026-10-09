/** Always null since justice v3: no justice is scored, because no method
 *  yet separates loyalty to the appointing president from career timing
 *  for an individual justice. Null is "not scored", never 0. */
export interface JusticeScore {
  loyalty: number | null;
  overall: number | null;
}

/** Shown, not scored (backend justice_loyalty): how many points more often
 *  the justice sided with the federal government while the appointing
 *  president was in office, as a share (0.145 = 14.5 points), the
 *  justice's own estimate with its standard error and 95% confidence
 *  interval (computed by the API); the votes under the appointing president
 *  and under others, and the share of each for the government. */
export interface JusticeLoyalty {
  estimate: number;
  se: number;
  ciLow: number;
  ciHigh: number;
  votesIn: number;
  votesOut: number;
  rateIn: number;
  rateOut: number;
  throughTerm: number | null;
}

export interface Justice {
  id: string;
  name: string;
  lastName: string;
  roleTitle: string;
  appointingPresident: string | null;
  appointingParty: string | null;
  dateStart: string | null;
  isActive: boolean;
  thumbnailUrl: string | null;
  score: JusticeScore;
  casesDecided: number;
  majorityPct: number;
  dissentPct: number;
  unanimousPct: number;
  authoredMajority: number;
  authoredDissent: number;
  authoredConcurrence: number;
  closeCaseMajorityPct: number;
  /** Agreement with each sitting justice, most first: the share of the
   *  cases both decided that they decided the same way. Optional only
   *  because a response cached before this field shipped can still be
   *  served; every live response carries it. */
  agreement?: { id: string; name: string; share: number }[];
  loyalty: JusticeLoyalty | null;
  /** Martin-Quinn position per term, [[term, position], ...], oldest first:
   *  shown, not scored. Negative is liberal, positive conservative. */
  idealPoints: [number, number][];
}

/** A sitting justice, listed by seniority and not ranked (justice v3). */
export interface JusticeLeaderboardEntry {
  id: string;
  name: string;
  lastName: string;
  roleTitle: string;
  appointingPresident: string | null;
  appointingParty: string | null;
  dateStart: string | null;
  isActive: boolean;
  thumbnailUrl: string | null;
  score: JusticeScore;
  casesDecided: number;
  majorityPct: number;
  dissentPct: number;
  loyalty: JusticeLoyalty | null;
}
