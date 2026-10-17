import type { CandidateSummary } from "@/types/election";

/** The next cycle as the official records stand (backend app/next_cycle.py):
 * `GET /api/elections/field`, `/new-congress` and `/fec-deadline`. Every
 * count is the backend's; the pages only word it. */

export interface NextField {
  cycle: number;
  /** FEC filings on the cycle's regular Senate and House races. */
  registered: number;
  house: {
    members: number;
    filedForHouse: number;
    noFiling: number;
  };
  /** Sitting members registered for another federal office: a House
   * member for the Senate or the presidency, a senator for the presidency. */
  filedForOtherOffice: {
    id: string;
    name: string;
    party: string;
    state: string;
    district: number | null;
    chamber: "H" | "S";
    office: "S" | "P";
    raceId: string;
  }[];
  senate: {
    seatsUp: number;
    racesOnFile: number;
    incumbentFiled: number;
    noIncumbentFiling: string[];
  };
  /** Races with a candidate the FEC codes as running for an open seat. */
  openSeats: number;
  sources: { fec: string };
}

export interface ChamberCompared {
  members: number;
  /** Members whose Congress.gov record is stored. */
  read: number;
  new: number;
  fromOtherChamber: number;
  returning: number;
  newShare: number | null;
  medianAge: number | null;
  ages: number;
}

export interface NewCongress {
  congress: number;
  firstYear: number;
  senate: ChamberCompared;
  house: ChamberCompared;
  sources: { congressGov: string };
}

export interface FecDeadlineEvent {
  date: string;
  label: string | null;
  reportType: string;
  reportYear: number | null;
  status: "upcoming" | "due_today" | "passed";
  daysLeft: number | null;
  daysAgo: number | null;
  /** Inside the window around its day: the event leads. */
  leads: boolean;
  source: string;
}

/** GET /api/elections/odd-year: the states holding regular state elections
 * in the coming odd year, by the constitution or law the scope file cites. */
export interface OddYearStates {
  year: number;
  states: {
    state: string;
    stateName: string;
    kinds: string[];
    words: string[];
    why: string | null;
    office: { url: string; label: string; sourceName: string };
  }[];
}

/** GET /api/elections/presidential (backend api/special_elections.py). */
export interface PresidentialContest {
  cycle: number;
  presidential: boolean;
  nextPresidential: number;
  electoralVotes: {
    states: { state: string; stateName: string; votes: number }[];
    total: number;
    toWin: number | null;
  };
  /** In a presidential cycle: the FEC's statutory candidates. */
  registered?: number;
  candidates?: CandidateSummary[];
  primaries?: { date: string; state: string; stateName: string; label: string }[];
}

/** GET /api/elections/administration: the EAC's survey, by state. */
export interface ElectionAdministration {
  year: number;
  version: number;
  url: string;
  items: Record<string, string>;
  states: {
    state: string;
    stateName: string;
    jurisdictions: number;
    items: Record<string, { value: number; reporting: number }>;
    rates: Record<"turnout" | "byMail" | "mailRejected" | "provisionalRejected", number | null>;
  }[];
  national: Record<
    "turnout" | "byMail" | "mailRejected" | "provisionalRejected",
    { rate: number | null; states: number }
  >;
  of: number;
}
