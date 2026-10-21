import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import NextField from "./NextField";
import NewCongress from "./NewCongress";
import FecDeadlineCard from "./FecDeadlineCard";
import BallotBasisNotice from "@/components/elections/BallotBasisNotice";
import { ballotHeadline } from "@/lib/elections";
import { describeStateBallot } from "@/lib/seo";
import type {
  FecDeadlineEvent,
  NewCongress as Compared,
  NextField as Field,
} from "@/types/nextCycle";

afterEach(cleanup);

const FIELD: Field = {
  cycle: 2028,
  registered: 412,
  house: { members: 433, filedForHouse: 301, noFiling: 131 },
  filedForOtherOffice: [
    {
      id: "jane-roe",
      name: "Jane Roe",
      party: "D",
      state: "OH",
      district: 9,
      chamber: "H",
      office: "S",
      raceId: "2028-SENATE-OH",
    },
    {
      id: "sam-poe",
      name: "Sam Poe",
      party: "R",
      state: "TX",
      district: null,
      chamber: "S",
      office: "P",
      raceId: "2028-PRES",
    },
  ],
  senate: { seatsUp: 34, racesOnFile: 30, incumbentFiled: 20, noIncumbentFiling: ["AL", "OH"] },
  openSeats: 12,
  sources: { fec: "https://www.fec.gov/data/candidates/" },
};

describe("NextField", () => {
  it("counts registrations, never ballot places or retirements", () => {
    const { container } = render(<NextField field={FIELD} />);
    expect(screen.getByRole("heading", { name: "Registered with the FEC for 2028" })).toBeTruthy();
    expect(screen.getByText("412 candidates have registered for federal races")).toBeTruthy();
    expect(screen.getByText("301 of 433")).toBeTruthy();
    expect(screen.getByText("20 of 34")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Jane Roe" })).toHaveAttribute(
      "href",
      "/politicians/jane-roe"
    );
    const text = container.textContent ?? "";
    expect(text).toMatch(/Sam Poe \(R-TX, senator\), for President/);
    expect(text).toMatch(/Jane Roe \(D-OH, representative\), for Ohio's Senate seat/);
    expect(text).toMatch(/No filing is not a retirement/);
    expect(text).not.toMatch(/is on the ballot|are on the ballot|retiring/i);
  });

  it("says when nobody has registered, and when it couldn't read the filings", () => {
    render(<NextField field={{ ...FIELD, registered: 0 }} />);
    expect(screen.getByText(/No candidate has registered/)).toBeTruthy();
    cleanup();
    render(<NextField field={null} />);
    expect(screen.getByText(/couldn't be read just now/)).toBeTruthy();
  });
});

const COMPARED: Compared = {
  congress: 120,
  firstYear: 2027,
  senate: {
    members: 100,
    read: 100,
    new: 11,
    fromOtherChamber: 6,
    returning: 0,
    newShare: 0.11,
    medianAge: 64.5,
    ages: 100,
  },
  house: {
    members: 434,
    read: 430,
    new: 72,
    fromOtherChamber: 0,
    returning: 2,
    newShare: 0.167,
    medianAge: 57,
    ages: 430,
  },
  sources: { congressGov: "https://www.congress.gov/members" },
};

describe("NewCongress", () => {
  it("compares turnover and age from the official record only", () => {
    const { container } = render(<NewCongress data={COMPARED} />);
    expect(screen.getByRole("heading", { name: "The 120th Congress, compared" })).toBeTruthy();
    expect(screen.getByText("11 new · 11%")).toBeTruthy();
    expect(screen.getByText(/6 from the other chamber; median age 65\./)).toBeTruthy();
    expect(screen.getByText(/records listing this Congress for 430 of 434 members/)).toBeTruthy();
    expect(container.textContent).toMatch(/aren't in the official record, so none is compared/);
  });

  it("says no record is stored rather than showing zero new members", () => {
    render(
      <NewCongress
        data={{ ...COMPARED, senate: { ...COMPARED.senate, read: 0, new: 0, newShare: null } }}
      />
    );
    expect(
      screen.getByText(/No member's Congress.gov record lists this Congress yet/)
    ).toBeTruthy();
  });
});

const EVENT: FecDeadlineEvent = {
  date: "2027-04-15",
  label: "April Quarterly",
  reportType: "Q1",
  reportYear: 2027,
  status: "upcoming",
  daysLeft: 5,
  daysAgo: null,
  leads: true,
  source: "https://www.fec.gov/help-candidates-and-committees/dates-and-deadlines/",
};

describe("FecDeadlineCard", () => {
  it("leads around the deadline and says when it falls", () => {
    render(<FecDeadlineCard event={EVENT} />);
    expect(
      screen.getByText(/April Quarterly reports \(2027\) are due in 5 days, on April 15, 2027/)
    ).toBeTruthy();
    cleanup();
    render(<FecDeadlineCard event={{ ...EVENT, status: "passed", daysLeft: null, daysAgo: 2 }} />);
    expect(screen.getByText(/were due 2 days ago/)).toBeTruthy();
  });

  it("stays away outside the window", () => {
    const { container } = render(<FecDeadlineCard event={{ ...EVENT, leads: false }} />);
    expect(container.textContent).toBe("");
  });
});

describe("FEC filers are never said to be on the ballot", () => {
  const ballot = {
    state: "OH",
    stateName: "Ohio",
    cycleYear: 2028,
    electionDate: "2028-11-07",
    measures: [],
    ballotBasis: { basis: "filers" },
  };
  it("in the headline, the count, the search wording and the notice", () => {
    const words = [
      ballotHeadline("Ohio", "filers"),
      describeStateBallot(ballot, false, null).title,
      describeStateBallot(ballot, false, null).description,
    ];
    const { container } = render(
      <BallotBasisNotice
        basis={{
          basis: "filers",
          primaryPassed: false,
          daysSincePrimary: null,
          supersededByPrimary: false,
        }}
      />
    );
    words.push(container.textContent ?? "");
    for (const w of words) {
      // "not on the ballot" is the point; "on the ballot" alone never.
      expect(w).not.toMatch(/(?<!not )on (the|[A-Z][a-z]+'s) ballot/);
      expect(w).not.toMatch(/What's on the/);
    }
    expect(words[0]).toBe("Everyone registered with the FEC to run in Ohio");
  });

  it("keeps the ballot wording once a state has confirmed its list", () => {
    expect(ballotHeadline("Ohio", "confirmed")).toBe(
      "Everyone on Ohio's ballot, and who is behind them"
    );
  });
});
