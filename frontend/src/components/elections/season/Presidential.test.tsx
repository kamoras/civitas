import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import PresidentialContest from "./PresidentialContest";
import ElectionAdministration from "./ElectionAdministration";
import type {
  ElectionAdministration as Admin,
  PresidentialContest as Contest,
} from "@/types/nextCycle";

afterEach(cleanup);

const CONTEST: Contest = {
  cycle: 2028,
  presidential: true,
  nextPresidential: 2028,
  electoralVotes: {
    states: [
      { state: "CA", stateName: "California", votes: 54 },
      { state: "DC", stateName: "District of Columbia", votes: 3 },
    ],
    total: 538,
    toWin: 270,
  },
  registered: 14,
  candidates: [
    {
      id: "P80000001",
      name: "DOE, JANE",
      party: "DEM",
      confirmed: false,
      incumbentChallenge: "O",
      hasRaisedFunds: true,
      candidateStatus: "C",
      contributions: 1250000,
      cashOnHand: null,
      lastFinancialsSync: "2028-01-31T00:00:00Z",
    } as unknown as NonNullable<Contest["candidates"]>[number],
  ],
  primaries: [{ date: "2028-02-06", state: "NV", stateName: "Nevada", label: "Primary election" }],
};

describe("PresidentialContest", () => {
  it("states the electoral votes, the FEC registrations and calls nothing", () => {
    const { container } = render(<PresidentialContest data={CONTEST} />);
    expect(screen.getByRole("heading", { name: "President · 2028" })).toBeTruthy();
    expect(screen.getByText("538 electoral votes · 270 to win")).toBeTruthy();
    expect(screen.getByLabelText("California: 54 electoral votes")).toBeTruthy();
    expect(screen.getByText(/the 1 who have raised the most/)).toBeTruthy();
    expect(screen.getByText("$1.3M")).toBeTruthy();
    expect(container.textContent).toMatch(/calls no race/);
    expect(container.textContent).toMatch(/None is on a ballot until a state certifies it/);
    expect(screen.getByText(/Nevada: Primary election/)).toBeTruthy();
  });

  it("is absent in a cycle with no presidential contest", () => {
    const { container } = render(
      <PresidentialContest data={{ ...CONTEST, presidential: false }} />
    );
    expect(container.textContent).toBe("");
  });
});

const ADMIN: Admin = {
  year: 2024,
  version: 2,
  url: "https://www.eac.gov/x.zip",
  items: {},
  of: 2,
  national: {
    turnout: { rate: 0.6612, states: 2 },
    byMail: { rate: 0.3, states: 2 },
    mailRejected: { rate: 0.0123, states: 1 },
    provisionalRejected: { rate: null, states: 0 },
  },
  states: [
    {
      state: "GA",
      stateName: "Georgia",
      jurisdictions: 159,
      items: {},
      rates: { turnout: 0.6433, byMail: 0.0507, mailRejected: 0.0174, provisionalRejected: null },
    },
  ],
};

describe("ElectionAdministration", () => {
  it("shows national rates over fully reporting states and a dash where a state reported in part", () => {
    render(<ElectionAdministration data={ADMIN} />);
    expect(screen.getByRole("heading", { name: "How the 2024 election was run" })).toBeTruthy();
    expect(screen.getByText("66.1%")).toBeTruthy();
    expect(screen.getAllByText(/1 of 2 states fully reporting/).length).toBe(1);
    const row = screen.getByRole("row", { name: /Georgia/ });
    expect(row.textContent).toBe("Georgia64.3%5.1%1.7%—");
  });
});
