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
      "Does their voting match what their state elected them to do? Each member's party-line break rate is compared to what their seat's partisan lean predicts, and their overall voting position (DW-NOMINATE, the standard roll-call measure) is compared to what a same-party member of a similarly-leaning seat typically holds. Party loyalty is not itself a penalty — voting the party line is how you represent the coalition that elected you, so below-expected defection sits at neutral, not below it — but a member positioned toward their party's flank relative to their seat's norm scores below neutral, and one positioned toward their seat's center scores above it. Crossing party lines earns extra credit only where it plausibly moves toward the state's political center. Neither defection nor bipartisanship is a virtue by itself — cross-party coalition-building is scored under Legislative Effectiveness, where the research supports it.",
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
      "How effective is this senator at advancing legislation? Based on bill passage rates, cosponsorship influence, and the bipartisan coalitions they attract to their own bills — members who draw cross-party cosponsors are substantially more successful at moving legislation (Harbridge-Yong, Volden & Wiseman 2023).",
  },
};
