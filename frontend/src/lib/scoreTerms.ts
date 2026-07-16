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
      "Does their voting match what their state elected them to do? Each member's party-line break rate is compared to what their seat's partisan lean predicts: matching it scores ~50 (typical for the seat), and hyper-loyalty in a swing state scores lower. Crossing party lines only earns extra credit where it moves toward the state's political center — party-line voting in a safe seat is representation, not a failing, and defection is not a virtue by itself.",
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
