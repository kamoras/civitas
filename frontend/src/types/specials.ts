import type { RegisterLink } from "@/types/election";
import type { RegistrationClosing } from "@/types/thisWeek";

/** Special elections, vacant seats and the federal election calendar, as
 * `GET /api/elections/specials` and `GET /api/elections/calendar` serve
 * them (backend app/special_elections.py). Every figure and sentence is the
 * backend's; the pages only lay them out. */

/** Where an election day stands on its state's own clock. */
export type DayStatus = "upcoming" | "voting" | "polls_closed" | "held";

/** The six stretches of the election calendar /elections and its hub are
 * laid out by (backend election_phase.season). */
export type SeasonName =
  "campaign" | "election_night" | "results" | "post_certification" | "off_season" | "ramp";

export interface Season {
  season: SeasonName;
  /** Between cycles (post-certification or off-season): /elections leads
   * with the archive, the specials and the calendar. */
  betweenCycles: boolean;
  /** The general election the season leads up to, or the one being
   * counted. */
  cycle: number;
  electionDay: string;
  /** The last general election held, the archive's; null before any. */
  heldCycle: number | null;
  /** The season's last day, where the calendar states it. */
  until: string | null;
  /** The cycle's first primary or caucus and last primary or runoff, by
   * the FEC's calendar; null until listed. */
  firstPrimary: string | null;
  lastPrimary: string | null;
  /** The section's pages, most useful first this season. */
  lead: string[];
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
  /** Read from this county election office's printing (the state's own
   *  source couldn't be read). */
  republishedBy?: string | null;
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
  /** The window registrationClosing covers, in days (voter_calendar.CLOSING_SOON_DAYS). */
  closingSoonDays?: number;
  fecDeadlines: FecDeadline[];
  nextThirtyDays: number;
  nextDate: string | null;
  sources: {
    electionDates: { url: string; readOn: string | null };
    reportingDates: { url: string; readOn: string | null };
  };
  season: Season;
}
