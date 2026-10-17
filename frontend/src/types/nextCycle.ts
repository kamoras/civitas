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
    filedForSenate: {
      id: string;
      name: string;
      party: string;
      state: string;
      district: number | null;
      office: "S";
      raceId: string;
    }[];
    noFiling: number;
  };
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
