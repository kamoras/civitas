import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import CycleArchive from "./CycleArchive";
import type { ArchiveRecord } from "@/types/electionArchive";
import type { LiveRaceResult } from "@/types/election";

const RESULT: LiveRaceResult = {
  raceId: "2026-SEN-GA",
  state: "GA",
  office: "S",
  district: null,
  isSpecial: false,
  heldBy: "DEM",
  official: true,
  votesCounted: 1000,
  reportingUnits: 159,
  totalUnits: 159,
  unitLabel: "counties",
  unitsComplete: true,
  sourceName: "Georgia Secretary of State",
  sourceUrl: "https://results.example/ga",
  fetchedAt: "2026-11-20T15:00:00Z",
  inLatestRead: true,
  lastChangeAt: "2026-11-10T15:00:00Z",
  leaderParty: "DEM",
  flip: false,
  candidates: [
    { name: "Dana Smith", party: "DEM", votes: 520, pct: 52.0, candidateId: "S1" },
    { name: "Ray Jones", party: "REP", votes: 480, pct: 48.0, candidateId: "S2" },
  ],
};

function record(overrides: Partial<ArchiveRecord> = {}): ArchiveRecord {
  return {
    cycle: 2026,
    electionDay: "2026-11-03",
    states: [
      { state: "GA", stateName: "Georgia", candidateListSource: "Georgia Secretary of State" },
      {
        state: "ME",
        stateName: "Maine",
        candidateListSource: null,
        stateContests: {
          statewideRaces: [
            {
              office: "governor",
              label: "Governor",
              nominees: [
                { party: "DEM", partyLabel: null, name: "Jo Park" },
                { party: "OTH", partyLabel: "Green Independent", name: "Kit Hale" },
              ],
            },
          ],
          stateLegRaces: [
            {
              chamber: "upper",
              label: "State Senate",
              districts: [
                { district: "12", nominees: [{ party: "REP", partyLabel: null, name: "Bo Tan" }] },
              ],
            },
          ],
          judicialRaces: [],
          statewideSource: { sourceName: "Maine Secretary of State", ballotList: false },
          judicialSource: { sourceName: null, ballotList: false },
        },
      },
    ],
    liveStates: ["GA"],
    rankedChoiceStates: ["ME"],
    redrawnStates: [],
    races: [
      {
        id: "2026-SEN-GA",
        state: "GA",
        office: "S",
        district: null,
        isSpecial: false,
        candidateSource: "confirmed",
        candidates: [
          {
            id: "S1",
            name: "SMITH, DANA",
            ballotName: "Dana Smith",
            party: "DEM",
            partyGroup: "DEM",
            partyLabel: "Democratic Party",
            confirmed: true,
            incumbentChallenge: "I",
          },
          {
            id: "S3",
            name: "LEE, SAM",
            ballotName: "Sam Lee",
            party: "LIB",
            partyGroup: "LIB",
            partyLabel: "Libertarian Party",
            confirmed: true,
            incumbentChallenge: "C",
          },
        ],
        result: RESULT,
      },
      {
        id: "2026-HOUSE-ME-2",
        state: "ME",
        office: "H",
        district: 2,
        isSpecial: false,
        candidateSource: "filers",
        candidates: [],
        result: null,
      },
    ],
    measures: [
      {
        id: "ga-1",
        state: "GA",
        electionDate: "2026-11-03",
        electionType: "general",
        number: "Amendment 1",
        title: "Amendment 1",
        measureType: null,
        origin: null,
        status: "certified",
        officialTitle: null,
        officialSummary: "Shall the Constitution of Georgia be amended so as to do a thing?",
        fiscalImpact: null,
        yesMeans: null,
        noMeans: null,
        titleAuthority: "General Assembly",
        fiscalAuthority: null,
        sourceName: "Georgia Secretary of State",
        sourceUrl: "https://sos.example/ga",
        republishedBy: null,
        asOf: "2026-09-28T00:00:00Z",
      },
    ],
    recordAsOf: "2026-11-20T15:00:00Z",
    mapAsOf: null,
    hasMap: false,
    stillCounting: false,
    totals: { races: 2, counted: 1, official: 1, measures: 1, stateContests: 2, states: 2 },
    ...overrides,
  };
}

function renderArchive(r: ArchiveRecord) {
  return render(
    <CycleArchive
      record={r}
      map={null}
      initialView="usual"
      initialChamber="senate"
      pageUrl="https://civitas.example/elections/2026"
    />
  );
}

describe("CycleArchive", () => {
  it("gives every race the anchor old race links resolve to", () => {
    const { container } = renderArchive(record());
    expect(container.querySelector("#race-2026-SEN-GA")).not.toBeNull();
    expect(container.querySelector("#race-2026-HOUSE-ME-2")).not.toBeNull();
    expect(container.querySelector("#state-GA")).not.toBeNull();
  });

  it("shows the count as the state reported it, and never calls the race", () => {
    renderArchive(record());
    expect(screen.getByText("Leads · Official count")).toBeTruthy();
    expect(screen.getByText(/Civitas calls no race/)).toBeTruthy();
    expect(screen.queryByText(/\bwon\b|\bwins\b/i)).toBeNull();
    expect(
      screen.getByRole("link", { name: /Georgia Secretary of State/ }).getAttribute("href")
    ).toBe("https://results.example/ga");
  });

  it("lists the ballot as printed, with its source, and says why a race has no count", () => {
    renderArchive(record());
    expect(screen.getByText("Sam Lee")).toBeTruthy();
    expect(screen.getByText(/Libertarian Party/)).toBeTruthy();
    expect(screen.getByText(/the state's certified list/)).toBeTruthy();
    expect(screen.getByText(/ranked-choice rounds/)).toBeTruthy();
    expect(screen.getByText(/No candidate list was on file/)).toBeTruthy();
  });

  it("keeps the state's own contests, with whose list and what it can miss", () => {
    renderArchive(record());
    expect(screen.getByText("Statewide offices")).toBeTruthy();
    expect(screen.getByText(/Kit Hale/)).toBeTruthy();
    expect(screen.getByText("(Green Independent)")).toBeTruthy();
    expect(screen.getByText("District 12")).toBeTruthy();
    expect(screen.getAllByText(/Names as listed by Maine Secretary of State/)[0]).toBeTruthy();
    expect(screen.getAllByText(/Names come from primary results/)[0]).toBeTruthy();
    expect(screen.getByText(/no count for the state's own contests/)).toBeTruthy();
  });

  it("quotes the measures verbatim with their drafter", () => {
    renderArchive(record());
    expect(screen.getByText(/Shall the Constitution of Georgia be amended/)).toBeTruthy();
    expect(screen.getByText(/Drafted by General Assembly/)).toBeTruthy();
  });

  it("says when no map was kept, and when the count is still moving", () => {
    renderArchive(record({ stillCounting: true }));
    expect(screen.getByText(/No pre-election map was kept/)).toBeTruthy();
    expect(screen.getByText(/The count is still coming in/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Live results/ }).getAttribute("href")).toBe(
      "/elections"
    );
  });
});
