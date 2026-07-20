export interface ScoreTerm {
  label: string;
  shortLabel: string; // for compact score bars
  description: string;
}

export type ScoreKey =
  | "fundingIndependence"
  | "independentVoting"
  | "fundingDiversity"
  | "legislativeEffectiveness";

export const SCORE_TERMS: Record<ScoreKey, ScoreTerm> = {
  fundingIndependence: {
    label: "Funding Independence",
    shortLabel: "FUNDING",
    description:
      "How free is this senator from PAC and mega-donor influence? Penalizes heavy reliance on PAC money and concentration in a few top donors.",
  },
  independentVoting: {
    label: "Constituent Alignment",
    shortLabel: "ALIGNMENT",
    description:
      "Does their voting match what their state elected them to do? Each member's party-line break rate is compared to what their seat's partisan lean predicts. Party loyalty is never itself a penalty — voting the party line is how you represent the coalition that elected you, so below-expected defection sits at neutral, not below it. Crossing party lines earns extra credit only where it plausibly moves toward the state's political center: a moderate breaking toward the middle counts, an extremist breaking from their own flank does not. Defection is not a virtue by itself.",
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
