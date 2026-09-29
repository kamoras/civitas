export interface JusticeScore {
  /** Independence from the appointing president, 0-100; null until the
   *  Supreme Court Database covers the justice. */
  loyalty: number | null;
  /** Backend-computed weighted total — never recompute this client-side. */
  overall: number | null;
}

/** The estimate behind the score (backend justice_loyalty): how many points
 *  more often the justice sided with the federal government while the
 *  appointing president was in office, as a share (0.145 = 14.5 points),
 *  shrunk across justices, with its standard error; the votes under the
 *  appointing president and under others, and the share of each for the
 *  government. */
export interface JusticeLoyalty {
  estimate: number;
  se: number;
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

export interface JusticeLeaderboardEntry {
  id: string;
  name: string;
  lastName: string;
  roleTitle: string;
  appointingPresident: string | null;
  appointingParty: string | null;
  isActive: boolean;
  thumbnailUrl: string | null;
  score: JusticeScore;
  casesDecided: number;
  majorityPct: number;
  dissentPct: number;
  loyalty: JusticeLoyalty | null;
}
