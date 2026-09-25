export interface ScoreTerm {
  label: string;
  shortLabel: string; // for compact score bars
  description: string;
}

export type ScoreKey =
  "fundingIndependence" | "constituentAlignment" | "fundingDiversity" | "legislativeEffectiveness";

export const SCORE_TERMS: Record<ScoreKey, ScoreTerm> = {
  fundingIndependence: {
    label: "Funding Independence",
    shortLabel: "FUNDING",
    description:
      "How free is this member from PAC and mega-donor influence? Blends PAC dependency (scaled by how close contributing PACs run to their legal caps), state-relative small-donor share, top-donor concentration, and industry concentration. Outside spending by super PACs is not counted: the member can't direct it, and it tracks how competitive the race is rather than how dependent the member is.",
  },
  constituentAlignment: {
    label: "Constituent Alignment",
    shortLabel: "ALIGNMENT",
    description:
      "Does their voting match what their state elected them to do? Each member's rate of breaking with their party is compared with how often members of the same party break in seats with the same partisan lean, measured from the chamber itself on every update. Breaking more often than that scores above neutral, and breaking less often scores below. Their voting position (the congress-specific Nokken-Poole roll-call measure) is compared with what a same-party member of a similarly-leaning seat typically holds: toward the party's flank scores below neutral, toward the seat's center above. The same rules apply in safe and competitive seats, because tests against House re-election results found voters in both respond alike. Cross-party coalition-building is scored under Legislative Effectiveness, where the research supports it.",
  },
  fundingDiversity: {
    label: "Funding Diversity",
    shortLabel: "DIVERSITY",
    description:
      "Is their funding spread across many industries, or dominated by a few? Blends source breadth (small-donor money counts most, opaque money least) with an inverse Herfindahl-Hirschman Index of industry concentration. Higher = more diverse funding sources. Not weighted into the overall score on its own; its industry-concentration part is one of Funding Independence's components.",
  },
  legislativeEffectiveness: {
    label: "Legislative Effectiveness",
    shortLabel: "EFFECTIVE",
    description:
      "How effective is this senator at advancing legislation? Based on bill passage rates, cosponsorship influence, and the bipartisan coalitions they attract to their own bills — members who draw cross-party cosponsors are substantially more successful at moving legislation (Harbridge-Yong, Volden & Wiseman 2023).",
  },
};
