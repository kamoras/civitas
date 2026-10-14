export type PoliticianBranch = "senate" | "house" | "president" | "scotus";

export interface PoliticianCard {
  id: string;
  branch: PoliticianBranch;
  name: string;
  party: string;
  state?: string;
  stateName?: string;
  district?: number | null;
  role: string;
  thumbnailUrl?: string | null;
  hasScorecard: boolean;
  overallScore?: number | null;
  activeIssueCount: number;
  isCurrent?: boolean;
  vacancyReason?: string | null;
  leftOfficeDate?: string | null;
  leadershipTitle?: string | null;
}

export interface Committee {
  committeeName: string;
  chamber: string;
  title?: string | null;
}

export interface PoliticianIdentity {
  name: string;
  party: string;
  role: string;
  state?: string;
  stateName?: string;
  district?: number | null;
  yearsInOffice?: number;
  contactFormUrl?: string;
  websiteUrl?: string;
  officePhone?: string;
  officeAddress?: string;
  vacancyReason?: string | null;
  leftOfficeDate?: string | null;
  leadershipTitle?: string | null;
  committees?: Committee[];
  // President
  number?: number;
  termStart?: string;
  termEnd?: string | null;
  isCurrent?: boolean;
  // Justice
  appointingPresident?: string;
  dateStart?: string;
  thumbnailUrl?: string | null;
  isActive?: boolean;
}

export interface PoliticianActiveIssue {
  id: number;
  publicId: string;
  title: string;
  summary: string;
  rank: number;
  date: string;
  firstSurfaced: string;
  policyAreas: string[];
}

export interface GovernmentDoc {
  id: number;
  docType: string;
  title: string;
  date: string;
  url?: string;
  source?: string;
}

export interface GovernmentRecord {
  totalDocs: number;
  recentDocs: GovernmentDoc[];
}

/** A member ranked next to the profile's, on the same leaderboard. */
export interface ChamberNeighbour {
  id: string;
  name: string;
  party: string;
  state: string;
  district: number | null;
  /** Overall score as displayed. */
  score: number;
  rank: number;
}

/** Every serving member of the profile's chamber, for its strip. */
export interface ChamberField {
  /** The profile's party, which `partyMedian` is of. */
  party: string;
  /** Each member's overall score as displayed, with their party, lowest first. */
  scores: { score: number; party: string }[];
  /** Null for a party with too few members to state one. */
  partyMedian: number | null;
  chamberMedian: number;
  above: ChamberNeighbour | null;
  below: ChamberNeighbour | null;
}

export interface PoliticianProfile {
  id: string;
  branch: PoliticianBranch;
  identity: PoliticianIdentity;
  hasScorecard: boolean;
  overallScore?: number | null;
  scorecard?: Record<string, unknown> | null;
  /** A serving member's place on their chamber's leaderboard. */
  chamberRank?: { rank: number; of: number } | null;
  /** The chamber the rank is taken over (api/politicians.py _chamber_field);
   *  null where there is no chamber rank. */
  chamberField?: ChamberField | null;
  /** The latest scoring run: its date and the scoring method's version. */
  scoredOn?: { date: string; algorithmVersion: string | null } | null;
  activeIssues: PoliticianActiveIssue[];
  governmentRecord: GovernmentRecord;
  /** The race this member is a candidate in on the site's election ballot
   * (backend member_on_ballot): their own seat or another office. Null
   * when nothing ties them to one, which is not a claim they aren't
   * running. Optional for an older backend. */
  onBallot?: {
    raceId: string;
    cycleYear: number;
    state: string;
    office: "S" | "H";
    district: number | null;
  } | null;
}

/** GET /api/politicians/house-districts: a state's House districts by
 *  county, on the lines of the Congress in office (the ones its sitting
 *  members were elected on). `available` is false when no bundled file
 *  holds that Congress's lines; `districts` is then empty. */
export interface HouseDistrictsByCounty {
  state: string;
  congress: number;
  available: boolean;
  /** `places`: the cities, towns and communities in the district on the
   * same lines (null when no place list holds them). */
  districts: { district: number; counties: string[]; places?: string[] | null }[];
}
