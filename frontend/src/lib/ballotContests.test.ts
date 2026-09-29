import { describe, expect, it } from "vitest";
import {
  buildBallotContests,
  contestForHash,
  contestHash,
  countBallotContests,
} from "./ballotContests";
import type { StateBallot } from "@/types/election";

function race(id: string, district: number | null) {
  return { id, district, isSpecial: false, candidates: [] };
}

function ballot(overrides: Partial<StateBallot> = {}): StateBallot {
  return {
    senateRaces: [race("2026-S-CT", null)],
    houseRaces: [1, 2, 3, 4, 5].map((d) => race(`2026-H-CT-0${d}`, d)),
    coverage: [],
    statewideCoverage: { status: "not_yet_covered" },
    statewideRaces: [],
    stateLegRaces: [],
    judicialCoverage: null,
    judicialRaces: [],
    measures: [],
    measureCoverage: { status: "not_yet_covered" },
    ...overrides,
  } as unknown as StateBallot;
}

describe("countBallotContests", () => {
  it("counts contests a voter marks, not page sections", () => {
    // Senate + one House district. The placeholders ("State offices — not
    // loaded yet", measures not loaded) and "News coverage" are sections.
    const b = ballot();
    const contests = buildBallotContests(b, false);
    expect(contests.length).toBeGreaterThan(2);
    expect(countBallotContests(contests, b)).toBe(2);
  });

  it("counts each statewide office, legislative chamber, court and measure", () => {
    const b = ballot({
      statewideCoverage: { status: "covered" },
      statewideRaces: [{}, {}, {}],
      stateLegRaces: [{ label: "State Senate", districts: [1, 2] }, { label: "State House", districts: [1] }],
      judicialCoverage: { status: "covered" },
      judicialRaces: [{}],
      measures: [{}, {}],
    } as unknown as Partial<StateBallot>);
    expect(countBallotContests(buildBallotContests(b, true), b)).toBe(2 + 3 + 2 + 1 + 2);
  });
});

describe("statewide ballot measures", () => {
  const m = (id: string, status: string) => ({ id, number: id, title: id, status });

  it("counts only measures still on the ballot", () => {
    const b = ballot({
      measureCoverage: { status: "covered" },
      measures: [m("1", "certified"), m("2", "removed"), m("3", "withdrawn")],
    } as unknown as Partial<StateBallot>);
    const contests = buildBallotContests(b, false);
    expect(contests.find((c) => c.kind === "measures")!.subtitle).toBe("1 measure");
    expect(countBallotContests(contests, b)).toBe(2 + 1);
  });

  it("never calls a dropped list that no read confirmed empty 'none'", () => {
    const b = ballot({
      measureCoverage: { status: "not_yet_covered" },
      measures: [m("SQ 1", "removed")],
    } as unknown as Partial<StateBallot>);
    const subtitle = buildBallotContests(b, false).find((c) => c.kind === "measures")!.subtitle;
    expect(subtitle).toBe("None current — check the official lookup");
  });

  it("does not word an operator's 'none' as the state's", () => {
    const b = ballot({
      measureCoverage: { status: "confirmed_none", basis: "operator" },
      measures: [],
    } as unknown as Partial<StateBallot>);
    expect(buildBallotContests(b, false).find((c) => c.kind === "measures")!.subtitle).toBe("None remaining");
  });
});

describe("statewide seats", () => {
  const gov = { office: "governor", label: "Governor", nominees: [] };
  const seat = (code: string, n: string, electedBy: "district" | "statewide" | null) => ({
    office: `${code}-${n}`,
    label: `Council, District ${n}`,
    officeCode: code,
    officeLabel: "Council",
    seat: n,
    electedBy,
    nominees: [],
  });

  it("counts a body elected by district as one office and one contest", () => {
    const b = ballot({
      statewideCoverage: { status: "covered", ballotList: true },
      statewideRaces: [gov, ...["1", "2", "3", "4", "5"].map((n) => seat("executive_council", n, "district"))],
    } as unknown as Partial<StateBallot>);
    const contests = buildBallotContests(b, false);
    const statewide = contests.find((c) => c.kind === "statewide")!;
    expect(statewide.subtitle).toBe("2 offices");
    expect(countBallotContests(contests, b)).toBe(2 + 2);
  });

  it("counts every seat of a body every voter votes for", () => {
    const b = ballot({
      statewideCoverage: { status: "covered" },
      statewideRaces: [seat("psc", "3", "statewide"), seat("psc", "5", "statewide")],
    } as unknown as Partial<StateBallot>);
    const contests = buildBallotContests(b, false);
    // Primary results: the box says so, since a shared image of it travels alone.
    expect(contests.find((c) => c.kind === "statewide")!.subtitle).toBe("1 office · from primary results");
    expect(countBallotContests(contests, b)).toBe(2 + 2);
  });

  it("gives no one-per-contest instruction where a contest fills two seats", () => {
    // North Dakota's PSC: two nominees per party, and a voter marks two.
    const nominee = (name: string, party: string) => ({ name, party });
    const psc = {
      office: "psc", label: "Public Service Commission",
      nominees: [nominee("A", "REP"), nominee("B", "REP"), nominee("C", "DEM"), nominee("D", "DEM")],
    };
    const b = ballot({ statewideCoverage: { status: "covered", ballotList: true }, statewideRaces: [gov, psc] } as unknown as Partial<StateBallot>);
    expect(buildBallotContests(b, false).find((c) => c.kind === "statewide")!.instruction).toBeNull();

    // Two nonpartisan nominees are rivals for one seat, not two seats.
    const supt = { office: "supt", label: "Superintendent", nominees: [nominee("E", "N"), nominee("F", "N")] };
    const one = ballot({ statewideCoverage: { status: "covered", ballotList: true }, statewideRaces: [gov, supt] } as unknown as Partial<StateBallot>);
    expect(buildBallotContests(one, false).find((c) => c.kind === "statewide")!.instruction).toBe("Vote for one in each");
  });

  it("says the legislature and judges came from primary results, as the statewide box does", () => {
    const fromPrimaries = ballot({
      statewideCoverage: { status: "covered" },
      stateLegRaces: [{ label: "State Senate", districts: [1] }],
      judicialCoverage: { status: "covered" },
      judicialRaces: [{}],
    } as unknown as Partial<StateBallot>);
    let contests = buildBallotContests(fromPrimaries, false);
    expect(contests.find((c) => c.kind === "stateleg")!.subtitle).toBe("Your seats depend on where you live · from primary results");
    expect(contests.find((c) => c.kind === "judicial")!.subtitle).toBe("1 court · from primary results");

    const certified = ballot({
      statewideCoverage: { status: "covered", ballotList: true },
      stateLegRaces: [{ label: "State Senate", districts: [1] }],
      judicialCoverage: { status: "covered", ballotList: true },
      judicialRaces: [{}],
    } as unknown as Partial<StateBallot>);
    contests = buildBallotContests(certified, false);
    expect(contests.find((c) => c.kind === "stateleg")!.subtitle).toBe("Your seats depend on where you live");
    expect(contests.find((c) => c.kind === "judicial")!.subtitle).toBe("1 court");
  });
});

// A shared image of a contest links to the fragment that reopens it; the
// page reads the same fragment back. The two must agree for every contest.
describe("contestHash", () => {
  it("round-trips through contestForHash for every contest", () => {
    const b = ballot();
    const contests = buildBallotContests(b, false);
    for (const c of contests) {
      const back = contestForHash(contestHash(c), contests, b);
      expect(back?.key).toBe(c.key);
    }
  });

  it("names the picked House district", () => {
    const b = ballot();
    const contests = buildBallotContests(b, false);
    const house = contests.find((c) => c.key === "house")!;
    const hash = contestHash(house, "2026-H-CT-03");
    expect(hash).toBe("#race-2026-H-CT-03");
    expect(contestForHash(hash, contests, b)).toEqual({ key: "house", houseRaceId: "2026-H-CT-03" });
  });
});
