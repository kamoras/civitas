import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import DayReportView from "./DayReportView";
import PeriodReportView from "./PeriodReportView";
import BillPageView from "./BillPageView";
import type { BillRecord, ChamberDay, CongressEvent, DayReport, PeriodReport, RollCallSummary, VoteDetail } from "@/types/congress";
import { longDate, shortDate } from "@/lib/congress";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));

const counts = { recordVotes: 0, billsPassed: 0, resolutionsPassed: 0, failed: 0, reported: 0, confirmed: 0, committeeMeetings: 0 };

function event(over: Partial<CongressEvent>): CongressEvent {
  return {
    kind: "passed", name: "", text: "", billId: null, billLabel: null, isResolution: false, nextStep: null,
    time: null, pages: "", date: "2026-09-24", chamber: "senate", ...over,
  };
}

function vote(over: Partial<RollCallSummary>): RollCallSummary {
  return {
    chamber: "senate", congress: 119, session: 2, number: 243, date: "2026-09-24", question: "On the Cloture Motion S. 4668",
    title: "", result: "Cloture Motion Agreed to", rejected: false, majorityRequirement: "3/5", yeas: 74, nays: 25,
    present: 0, notVoting: 1, billId: "S.4668", billLabel: "S. 4668", sourceUrl: "", ...over,
  };
}

function chamber(over: Partial<ChamberDay>): ChamberDay {
  return {
    chamber: "senate", status: "final", convenedAt: "10 a.m.", adjournedAt: "4:05 p.m.", minutesInSession: 365,
    adjournmentText: "", nextMeeting: "3 p.m., Monday, September 28", nextProgram: "", billsIntroduced: 74,
    resolutionsIntroduced: 14, introducedText: "Seventy-four bills and fourteen resolutions were introduced.",
    source: "digest", sourceUrl: "https://www.govinfo.gov/app/details/CREC-2026-09-24", fetchedAt: null,
    floorLogStatus: "ok", counts, sentence: "", votes: [], passed: [], failed: [], reported: [], confirmed: [],
    committees: [], floorLog: [], ...over,
  };
}

const day: DayReport = {
  date: "2026-09-24",
  sentence: "The Senate passed 1 bill and took 2 record votes. The House met for 3 minutes and took no record votes.",
  previousDay: "2026-09-23",
  nextDay: null,
  syncedAt: null,
  chambers: {
    senate: chamber({
      counts: { ...counts, recordVotes: 2, billsPassed: 1, failed: 1 },
      votes: [vote({}), vote({ number: 244, question: "On the Concurrent Resolution", result: "Concurrent Resolution Rejected", rejected: true, yeas: 49, nays: 50, billId: "HCONRES.89", billLabel: "H. Con. Res. 89" })],
      passed: [event({ billId: "HR.2388", billLabel: "H.R. 2388", name: "Lower Elwha Klallam Tribe Project Lands Restoration Act", text: "Senate passed H.R. 2388, to take certain Federal land ...", nextStep: "both" })],
      failed: [event({ kind: "failed", billId: "HCONRES.89", billLabel: "H. Con. Res. 89", name: "Hostilities with Iran", text: "By 49 yeas to 50 nays (Vote No. 244), Senate did not agree to H. Con. Res. 89 ..." })],
      committees: [event({ kind: "committee", name: "Committee on Finance", text: "Committee ordered favorably reported ..." })],
    }),
    house: chamber({
      chamber: "house", convenedAt: "2:30 p.m.", adjournedAt: "2:33 p.m.", minutesInSession: 3, billsIntroduced: 85,
      introducedText: "85 public bills ... were introduced.",
      floorLog: [event({ kind: "floor", chamber: "house", time: "2:30:00 P.M.", text: "The House convened, starting a new legislative day." })],
    }),
  },
};

async function violations() {
  const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
}

afterEach(() => vi.unstubAllGlobals());

describe("congress formatting", () => {
  it("reads a date as a calendar date in any time zone", () => {
    expect(longDate("2026-09-24")).toBe("Thursday, September 24, 2026");
    expect(shortDate("2026-01-01")).toBe("Thu, Jan 1");
  });
});

describe("day report", () => {
  it("shows the day as the API computed it", () => {
    render(<DayReportView report={day} />);
    expect(screen.getByRole("heading", { level: 1, name: "Thursday, September 24, 2026" })).toBeTruthy();
    expect(screen.getByText(day.sentence)).toBeTruthy();
    expect(screen.getByText("Passed both chambers")).toBeTruthy();
    // A bill links to its page; a vote on a bill opens that vote there.
    expect(screen.getByRole("link", { name: /H\.R\. 2388/ }).getAttribute("href")).toBe("/congress/bills/HR.2388");
    expect(screen.getByRole("link", { name: "S. 4668" }).getAttribute("href")).toBe("/congress/bills/S.4668#vote-senate-2-243");
    expect(screen.getByRole("link", { name: /Wed, Sep 23/ }).getAttribute("href")).toBe("/congress/2026-09-23");
  });

  it("says a live day is waiting for the Digest, and a dead floor log is not empty", () => {
    const live: DayReport = { ...day, chambers: { ...day.chambers, house: chamber({ chamber: "house", status: "live", floorLogStatus: "failed" }) } };
    render(<DayReportView report={live} />);
    expect(screen.getByText(/appear here when the Congressional Record/)).toBeTruthy();
    expect(screen.getByText(/floor log could not be read/)).toBeTruthy();
  });

  it("has no axe violations", async () => {
    render(<DayReportView report={day} />);
    expect(await violations()).toEqual([]);
  });
});

const week: PeriodReport = {
  start: "2026-09-21", end: "2026-09-27",
  sentence: "The Senate met 3 days, took 6 record votes and passed 11 bills. The House met 2 days and took no record votes.",
  totals: {
    senate: { ...counts, recordVotes: 6, billsPassed: 11, daysInSession: 3, billsIntroduced: 123, resolutionsIntroduced: 43, daysPending: 0 },
    house: { ...counts, daysInSession: 2, billsIntroduced: 105, resolutionsIntroduced: 22, daysPending: 0 },
  },
  days: ["21", "22", "23", "24", "25", "26", "27"].map((d) => ({
    date: `2026-09-${d}`,
    noRecordPublished: d === "26",
    senate: { inSession: d !== "21", recorded: true, convenedAt: null, adjournedAt: null, minutesInSession: null, counts },
    house: { inSession: d === "21" || d === "24", recorded: true, convenedAt: null, adjournedAt: null, minutesInSession: 3, counts },
  })),
  passedBothChambers: [event({ billId: "HR.2388", billLabel: "H.R. 2388", name: "Lower Elwha Klallam Tribe Project Lands Restoration Act" })],
  passedOneChamber: [event({ billId: "S.3257", billLabel: "S. 3257", name: "John A. Hauser Mental Health in Aviation Act" })],
  closestVotes: [vote({ number: 244, yeas: 49, nays: 50, result: "Rejected", rejected: true, billId: "HCONRES.89", billLabel: "H. Con. Res. 89" })],
  votes: [vote({})],
  becameLaw: [],
  syncedAt: null,
  previous: "2026-09-14",
  next: "2026-09-28",
};

describe("week report", () => {
  it("links each day and each bill", () => {
    render(<PeriodReportView report={week} kind="week" />);
    expect(screen.getByRole("heading", { level: 1, name: "Week of September 21, 2026" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Thu, Sep 24/ }).getAttribute("href")).toBe("/congress/2026-09-24");
    expect(screen.getByRole("link", { name: "S. 3257" }).getAttribute("href")).toBe("/congress/bills/S.3257");
    // A day with no Congressional Record says so, rather than a dash.
    expect(within(screen.getByRole("link", { name: /Sat, Sep 26/ })).getAllByText("No Record")).toHaveLength(2);
  });

  it("has no axe violations", async () => {
    render(<PeriodReportView report={week} kind="week" />);
    expect(await violations()).toEqual([]);
  });
});

const record: BillRecord = {
  billId: "S.4668", billLabel: "S. 4668", congress: 119, title: "Protect College Sports Act of 2026",
  introducedDate: "2026-06-02", originChamber: "Senate", policyArea: "Sports and Recreation",
  latestAction: { actionDate: "2026-09-24", text: "The committee substitute tabled by Voice Vote." },
  sponsors: [{ name: "Sen. Cruz, Ted [R-TX]", party: "R", state: "TX", district: null, bioguideId: "C001098", isOriginalCosponsor: null, sponsorshipDate: null, page: "/politicians/ted-cruz" }],
  cosponsors: [],
  cboCostEstimates: [],
  summary: { actionDesc: "Reported to Senate", actionDate: "2026-06-24", paragraphs: ["Protect College Sports Act of 2026", "This bill establishes requirements for NIL agreements."] },
  actions: [{ date: "2026-09-24", text: "Cloture on the measure, as amended, invoked in Senate by Yea-Nay Vote. 74 - 25.", type: "Floor", rollCalls: [] }],
  textVersions: [{ type: "Reported to Senate", date: "2026-06-24", formats: { PDF: "https://www.congress.gov/x.pdf" } }],
  votes: [{ chamber: "senate", session: 2, number: 243, date: "2026-09-24", question: "On the Cloture Motion S. 4668", title: "", result: "Cloture Motion Agreed to", rejected: false, yeas: 74, nays: 25, present: 0, notVoting: 1, parties: [{ party: "R", yea: 49, nay: 3, present: 0, notVoting: 1 }] }],
  days: [{ date: "2026-09-24", chamber: "senate", entries: [] }],
  congressGovUrl: "https://www.congress.gov/bill/119th-congress/senate-bill/4668",
  unavailable: ["cosponsors"],
};

const detail: VoteDetail = {
  ...vote({}),
  parties: record.votes[0].parties,
  members: [
    { lastName: "Cruz", firstName: "Ted", party: "R", state: "TX", position: "Yea", bucket: "yea", page: "/politicians/ted-cruz" },
    { lastName: "Paul", firstName: "Rand", party: "R", state: "KY", position: "Nay", bucket: "nay", page: null },
  ],
};

describe("bill page", () => {
  it("shows the record, every member's vote, and filters by state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => detail }));
    render(<BillPageView billId="S.4668" record={record} detail={null} stageName={null} />);
    expect(screen.getByRole("heading", { level: 1, name: "Protect College Sports Act of 2026" })).toBeTruthy();
    // A part Congress.gov did not return is said to be missing, not shown empty.
    expect(screen.getByText(/did not return the cosponsors/)).toBeTruthy();
    expect(await screen.findByRole("link", { name: "Ted Cruz" })).toBeTruthy();
    expect(screen.getByText("Rand Paul")).toBeTruthy();
    await userEvent.selectOptions(screen.getByLabelText("State"), "KY");
    expect(screen.queryByRole("link", { name: "Ted Cruz" })).toBeNull();
    expect(within(screen.getByRole("link", { name: /Thu, Sep 24, 2026/ })).getByText(/Sep 24/)).toBeTruthy();
  });

  it("has no axe violations", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => detail }));
    render(<BillPageView billId="S.4668" record={record} detail={null} stageName={null} />);
    await screen.findByRole("link", { name: "Ted Cruz" });
    expect(await violations()).toEqual([]);
  });
});
