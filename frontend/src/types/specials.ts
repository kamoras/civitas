import type { RegisterLink } from "@/types/election";
import type { RegistrationClosing } from "@/types/thisWeek";

/** Special elections, vacant seats and the federal election calendar, as
 * `GET /api/elections/specials` and `GET /api/elections/calendar` serve
 * them (backend app/special_elections.py). Every figure and sentence is the
 * backend's; the pages only lay them out. */

/** Where an election day stands on its state's own clock. */
export type DayStatus = "upcoming" | "voting" | "polls_closed" | "held";

export interface Season {
  season: "campaign" | "results" | "post_election" | "off_season";
  /** Between cycles (post-election or off-season): /elections leads with
   * the specials and the calendar. */
  betweenCycles: boolean;
  cycle: number;
  firstPrimary: string | null;
}

export interface UsualVote {
  lean: number;
  label: string;
  source: "computed" | "state";
  congress: number | null;
}

export interface SpecialSwing {
  result: number;
  resultLabel: string;
  usualVote: UsualVote;
  swing: number;
  toward: "D" | "R" | null;
  final: boolean;
  sentence: string;
}

export interface SpecialCount {
  tallies: { name: string; party: string | null; votes: number }[];
  votesCounted: number;
  reportingUnits: number | null;
  totalUnits: number | null;
  unitLabel: string | null;
  unitsComplete: boolean | null;
  official: boolean;
  sourceName: string | null;
  sourceUrl: string | null;
  lastChangeAt: string | null;
}

export interface SpecialElection {
  id: string;
  date: string;
  state: string;
  stateName: string;
  office: "H" | "S";
  district: number | null;
  seat: string;
  typeCode: string;
  label: string | null;
  note: string | null;
  status: DayStatus;
  daysLeft: number | null;
  /** Why there is or isn't a count: read from the state's feed, a type not
   * read (a special primary or convention), or a state whose feed Civitas
   * has no reader for. */
  liveCount: "read" | "not_read_type" | "not_covered";
  readStatus: string | null;
  count: SpecialCount | null;
  swing: SpecialSwing | null;
}

export interface SeatVacancy {
  id: string;
  office: "H" | "S";
  state: string;
  stateName: string;
  district: number | null;
  senateClass: number | null;
  seat: string;
  predecessor: string | null;
  predecessorParty: string | null;
  vacatedOn: string | null;
  footnote: string | null;
  sourceUrl: string;
  readAt: string | null;
  nextSpecial: SpecialElection | null;
}

export interface SpecialsBoard {
  asOf: string | null;
  vacancies: SeatVacancy[];
  vacanciesReadAt: string | null;
  upcoming: SpecialElection[];
  recent: SpecialElection[];
  lastSpecial: SpecialElection | null;
  averageSwing: {
    swing: number | null;
    count: number;
    of: number;
    since: string;
    sentence: string | null;
  };
  sources: { fec: string };
  season: Season;
}

export interface CalendarElection {
  id: string;
  date: string;
  state: string;
  stateName: string;
  office: string;
  district: number | null;
  seat: string;
  typeCode: string;
  label: string | null;
  special: boolean;
  note: string | null;
  status: DayStatus;
  daysLeft: number | null;
}

export interface CalendarDeadline {
  state: string;
  stateName: string;
  kind: string;
  label: string;
  sentence: string;
  date: string;
  status: "upcoming" | "open" | "closed";
  daysLeft: number | null;
  source: { publisher: string | null; url: string; type: string };
  register?: RegisterLink | null;
}

export interface FecDeadline {
  date: string;
  reportType: string;
  label: string;
  reportYear: number | null;
  status: "upcoming" | "due_today" | "held";
  daysLeft: number | null;
}

export interface ElectionCalendar {
  today: string;
  window: { from: string; to: string };
  elections: CalendarElection[];
  voterDeadlines: CalendarDeadline[];
  voterDeadlinesFor: string;
  /** Every state and DC, for the state filter, whether or not its
   *  deadlines could be read. Absent from an older API. */
  states?: {
    state: string;
    stateName: string;
    deadlinesRead: boolean;
    register: RegisterLink | null;
  }[];
  registrationClosing?: RegistrationClosing[];
  fecDeadlines: FecDeadline[];
  nextThirtyDays: number;
  nextDate: string | null;
  sources: {
    electionDates: { url: string; readOn: string | null };
    reportingDates: { url: string; readOn: string | null };
  };
  season: Season;
}
