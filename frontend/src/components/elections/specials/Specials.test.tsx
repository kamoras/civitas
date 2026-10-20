import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ElectionCalendar, Season, SpecialElection, SpecialsBoard } from "@/types/specials";
import SpecialsBoardView, { noCountText } from "./Specials";
import OffSeasonLead from "./OffSeasonLead";
import CalendarList from "./CalendarList";
import { calendarEntries } from "@/lib/specials";

afterEach(cleanup);

const SEASON: Season = {
  season: "off_season",
  betweenCycles: true,
  cycle: 2028,
  electionDay: "2028-11-07",
  heldCycle: 2026,
  until: "2028-03-06",
  firstPrimary: "2028-03-07",
  lastPrimary: "2028-09-12",
  lead: ["calendar", "archive", "ballots", "redistricting", "map"],
};

function special(over: Partial<SpecialElection> = {}): SpecialElection {
  return {
    id: "2027-03-02-TX-H-7-SG",
    date: "2027-03-02",
    state: "TX",
    stateName: "Texas",
    office: "H",
    district: 7,
    seat: "TX-7",
    typeCode: "SG",
    label: "Special election general",
    note: "Rep. A's Seat.",
    status: "upcoming",
    daysLeft: 29,
    liveCount: "read",
    readStatus: null,
    count: null,
    swing: null,
    ...over,
  };
}

const HELD = special({
  id: "2026-12-01-TN-H-7-SG",
  date: "2026-12-01",
  state: "TN",
  stateName: "Tennessee",
  seat: "TN-7",
  status: "held",
  daysLeft: null,
  readStatus: "ok",
  count: {
    tallies: [
      { name: "Bo Roe", party: "R", votes: 52000 },
      { name: "Ann Doe", party: "D", votes: 48000 },
    ],
    votesCounted: 100000,
    reportingUnits: 10,
    totalUnits: 10,
    unitLabel: "counties",
    unitsComplete: true,
    official: false,
    sourceName: "Tennessee Secretary of State",
    sourceUrl: "https://results.example/tn",
    lastChangeAt: "2026-12-02T04:00:00Z",
  },
  swing: {
    result: 2,
    resultLabel: "R+2",
    usualVote: { lean: 13, label: "R+13", source: "computed", congress: 119 },
    swing: -11,
    toward: "D",
    final: false,
    sentence:
      "The result was R+2 against a usual vote of R+13: 11 points toward Democrats, on the count as it stands (not final).",
  },
});

function board(over: Partial<SpecialsBoard> = {}): SpecialsBoard {
  return {
    asOf: "2027-02-01",
    vacancies: [
      {
        id: "H-TX-7",
        office: "H",
        state: "TX",
        stateName: "Texas",
        district: 7,
        senateClass: null,
        seat: "TX-7",
        predecessor: "A Member",
        predecessorParty: "R",
        vacatedOn: "2026-12-01",
        footnote: "Vacancy due to the resignation of A Member, December 1, 2026.",
        sourceUrl: "https://clerk.house.gov/xml/lists/MemberData.xml",
        readAt: "2027-02-01T08:00:00Z",
        nextSpecial: special(),
      },
    ],
    vacanciesReadAt: "2027-02-01T08:00:00Z",
    upcoming: [special()],
    recent: [HELD],
    lastSpecial: null,
    averageSwing: {
      swing: -11,
      count: 1,
      of: 1,
      since: "2026-02-01",
      sentence:
        "Average swing against the seats' usual vote: 11 points toward Democrats, over 1 of the 1 specials held in the last year with a settled count between a Democrat and a Republican.",
    },
    sources: { fec: "https://www.fec.gov/data/election-dates/" },
    season: SEASON,
    ...over,
  };
}

const CAL: ElectionCalendar = {
  today: "2027-02-01",
  window: { from: "2027-01-01", to: "2028-02-01" },
  elections: [
    {
      id: "2027-03-02-TX-H-7-SG",
      date: "2027-03-02",
      state: "TX",
      stateName: "Texas",
      office: "H",
      district: 7,
      seat: "TX-7",
      typeCode: "SG",
      label: "Special election general",
      special: true,
      note: null,
      status: "upcoming",
      daysLeft: 29,
    },
    {
      id: "2027-11-02-VA-S-0-G",
      date: "2027-11-02",
      state: "VA",
      stateName: "Virginia",
      office: "S",
      district: null,
      seat: "Virginia: U.S. Senate",
      typeCode: "G",
      label: "General election",
      special: false,
      note: null,
      status: "upcoming",
      daysLeft: 274,
    },
  ],
  voterDeadlines: [
    {
      state: "TX",
      stateName: "Texas",
      kind: "registration",
      label: "Registration",
      sentence: "Registration closes Oct 5",
      date: "2027-10-05",
      status: "upcoming",
      daysLeft: 246,
      source: { publisher: "Texas SOS", url: "https://www.sos.state.tx.us/", type: "official" },
    },
    {
      state: "VA",
      stateName: "Virginia",
      kind: "registration",
      label: "Registration",
      sentence: "Registration closes Oct 5",
      date: "2027-10-05",
      status: "upcoming",
      daysLeft: 246,
      source: {
        publisher: "Virginia ELECT",
        url: "https://www.elections.virginia.gov/",
        type: "official",
      },
    },
  ],
  voterDeadlinesFor: "2028-11-07",
  fecDeadlines: [
    {
      date: "2027-01-31",
      reportType: "YE",
      label: "YEAR-END",
      reportYear: 2026,
      status: "held",
      daysLeft: null,
    },
    {
      date: "2027-04-15",
      reportType: "Q1",
      label: "APRIL QUARTERLY",
      reportYear: 2027,
      status: "upcoming",
      daysLeft: 73,
    },
  ],
  nextThirtyDays: 1,
  nextDate: "2027-03-02",
  sources: {
    electionDates: { url: "https://www.fec.gov/data/election-dates/", readOn: "2027-02-01" },
    reportingDates: { url: "https://www.fec.gov/", readOn: "2027-02-01" },
  },
  season: SEASON,
};

describe("SpecialsBoardView", () => {
  it("shows a vacancy with its scheduled special, and a held special's count and swing", () => {
    render(<SpecialsBoardView board={board()} />);
    expect(screen.getByText(/Vacancy due to the resignation of A Member/)).toBeTruthy();
    expect(screen.getByText(/Next: Special election general, March 2, 2027/)).toBeTruthy();
    expect(screen.getByText("IN 29 DAYS", { exact: false })).toBeTruthy();
    // Never a call: the count leads, not final until the source says so.
    expect(screen.getByText("LEADING · NOT FINAL")).toBeTruthy();
    expect(screen.getByText(/11 points toward Democrats, on the count as it stands/)).toBeTruthy();
    expect(screen.getByText(/Average swing against the seats' usual vote/)).toBeTruthy();
  });

  it("says so when every list is empty, and names the last special", () => {
    render(
      <SpecialsBoardView
        board={board({
          vacancies: [],
          upcoming: [],
          recent: [],
          lastSpecial: HELD,
          averageSwing: { swing: null, count: 0, of: 0, since: "2026-02-01", sentence: null },
        })}
      />
    );
    expect(screen.getByText(/Every seat in Congress is filled/)).toBeTruthy();
    expect(screen.getByText(/No federal special election is on the FEC's calendar/)).toBeTruthy();
    expect(
      screen.getByText(/last federal special election on file was for TN-7, on December 1, 2026/)
    ).toBeTruthy();
  });

  it("says which absence a missing count is", () => {
    expect(noCountText(special({ liveCount: "not_covered" }))).toMatch(/no reader for Texas/);
    expect(noCountText(special({ typeCode: "SP", liveCount: "not_read_type" }))).toMatch(/primary/);
    expect(noCountText(special({ status: "held", readStatus: "failed" }))).toMatch(
      /couldn't be read/
    );
    expect(noCountText(HELD)).toBeNull();
  });
});

describe("the calendar", () => {
  it("puts every kind of date in one order", () => {
    expect(calendarEntries(CAL).map((e) => e.tag)).toEqual([
      "FEC REPORT",
      "SPECIAL",
      "FEC REPORT",
      "DEADLINE",
      "DEADLINE",
      "ELECTION",
    ]);
  });

  it("filters by state in the browser and keeps national deadlines", () => {
    render(<CalendarList cal={CAL} />);
    expect(screen.getByText(/Virginia: U.S. Senate/)).toBeTruthy();
    // Every state: one line per date for its voter deadlines.
    expect(screen.getByText("Voter deadlines in 2 states")).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "TX" } });
    expect(screen.queryByText(/Virginia: U.S. Senate/)).toBeNull();
    expect(screen.getByText(/april quarterly/)).toBeTruthy();
    expect(screen.getByText("Texas: Registration closes Oct 5")).toBeTruthy();
    expect(screen.queryByText(/Virginia: Registration/)).toBeNull();
    expect(screen.getByText(/Passed in the last month \(1\)/)).toBeTruthy();
  });
});

describe("CalendarList state filter", () => {
  it("lists every state the API names, one whose deadlines couldn't be read included", () => {
    render(
      <CalendarList
        cal={{
          ...CAL,
          states: [
            { state: "NY", stateName: "New York", deadlinesRead: false, register: null },
            { state: "TX", stateName: "Texas", deadlinesRead: true, register: null },
          ],
        }}
      />
    );
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Every state", "New York (deadlines not read)", "Texas"]);
  });
});

describe("OffSeasonLead", () => {
  it("leads with the specials and what is next, and says when the calendar is unread", () => {
    const { rerender } = render(<OffSeasonLead board={board()} cal={CAL} />);
    expect(screen.getByText(/The 2028 primaries begin on March 7, 2028/)).toBeTruthy();
    expect(screen.getAllByText(/TX-7/).length).toBeGreaterThan(0);
    rerender(<OffSeasonLead board={board()} cal={null} />);
    expect(screen.getByText(/The calendar couldn't be read just now/)).toBeTruthy();
  });
});
