export interface ScoreTerm {
  label: string;
  shortLabel: string; // for compact score bars
  description: string;
}

export type ScoreKey =
  | "fundingIndependence"
  | "promisePersistence"
  | "independentVoting"
  | "fundingDiversity"
  | "legislativeEffectiveness";

export const TECHNICAL_TERMS: Record<ScoreKey, ScoreTerm> = {
  fundingIndependence: {
    label: "Funding Independence",
    shortLabel: "FUNDING",
    description:
      "How free is this senator from PAC and mega-donor influence? Penalizes heavy reliance on PAC money and concentration in a few top donors.",
  },
  promisePersistence: {
    label: "Promise Persistence",
    shortLabel: "PROMISES",
    description:
      "Are they keeping campaign promises? Compares stated platform commitments against actual votes, using AI analysis. Higher = more follow-through.",
  },
  independentVoting: {
    label: "Independent Voting",
    shortLabel: "INDEP. VOTE",
    description:
      "How often do they vote against their own party? Adjusted for state partisanship — breaking party line in a swing state counts less than in a deep-red/blue state.",
  },
  fundingDiversity: {
    label: "Funding Diversity",
    shortLabel: "DIVERSITY",
    description:
      "Is their funding spread across many industries, or dominated by a few? Uses Shannon entropy to measure concentration. Higher = more diverse funding sources.",
  },
  legislativeEffectiveness: {
    label: "Legislative Effectiveness",
    shortLabel: "EFFECTIVE",
    description:
      "How effective is this senator at advancing legislation? Based on bill passage rates, cosponsorship influence, and ability to move bills through the process.",
  },
};

export const PLAIN_TERMS: Record<ScoreKey, ScoreTerm> = {
  fundingIndependence: {
    label: "PAC Money Reliance",
    shortLabel: "PAC $",
    description:
      "How much they rely on PAC money and large donors vs. regular people. Higher = less dependent on big-money interests.",
  },
  promisePersistence: {
    label: "Keeps Promises",
    shortLabel: "PROMISES",
    description:
      "How often they keep the promises they made during their campaign. We compare what they said they'd do against how they actually voted.",
  },
  independentVoting: {
    label: "Breaks With Party",
    shortLabel: "INDEP.",
    description:
      "How often they break with their party to vote differently. Adjusted for their district — a representative in a split district gets more credit for bipartisan votes.",
  },
  fundingDiversity: {
    label: "Spread Out Funding",
    shortLabel: "DIVERSITY",
    description:
      "How spread out their funding sources are across different industries. Higher = no single industry is bankrolling them.",
  },
  legislativeEffectiveness: {
    label: "Gets Things Done",
    shortLabel: "EFFECTIVE",
    description:
      "How often their bills actually pass or move forward, and how much influence they have getting other lawmakers to support legislation.",
  },
};
