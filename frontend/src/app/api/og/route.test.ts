import { describe, expect, it } from "vitest";
import { findRace, formatStanding, parseIssueId, parseStateCode } from "./route";
import type { RaceWithCandidates, StateBallot } from "@/types/election";

describe("formatStanding", () => {
  it("formats a senator as party-state, no district", () => {
    expect(formatStanding({ party: "D", state: "CA" })).toBe("D-CA");
  });

  it("formats a representative as party-state-district", () => {
    expect(formatStanding({ party: "R", state: "TX", district: 12 })).toBe("R-TX-12");
  });

  // FEC codes at-large districts as "00" -> district 0. The bug this
  // guards against: a truthy `identity?.district ?` check treated 0 like
  // "no district", silently rendering an at-large representative in the
  // Senate's party-state format instead of party-state-AL. Same bug
  // class already guarded in lib/elections.test.ts.
  it("formats an at-large district (0) as AL, not as a missing district", () => {
    expect(formatStanding({ party: "D", state: "AK", district: 0 })).toBe("D-AK-AL");
  });

  it("returns an empty string when neither state nor district is known", () => {
    expect(formatStanding({})).toBe("");
    expect(formatStanding(undefined)).toBe("");
  });
});

describe("parseIssueId", () => {
  // The bug this guards against: every real link on the site points at
  // issue.publicId ("i" + 8 hex chars, e.g. "i9e3779b1"), never the raw
  // numeric id — a digits-only check here rejected every real request
  // and silently fell through to the generic fallback card for every
  // issue, photo or not, regardless of how much real content it had.
  it("accepts a real public id", () => {
    expect(parseIssueId("i9e3779b1")).toBe("i9e3779b1");
  });

  it("accepts a public id regardless of letter case", () => {
    expect(parseIssueId("I9E3779B1")).toBe("I9E3779B1");
  });

  it("still accepts a legacy bare numeric id", () => {
    expect(parseIssueId("650")).toBe("650");
  });

  it("rejects a public id with the wrong hex length", () => {
    expect(parseIssueId("i9e3779b")).toBeNull();
    expect(parseIssueId("i9e3779b12")).toBeNull();
  });

  it("rejects a non-hex suffix", () => {
    expect(parseIssueId("i9e3779zz")).toBeNull();
  });

  it("rejects garbage and empty input", () => {
    expect(parseIssueId("not-an-id")).toBeNull();
    expect(parseIssueId("")).toBeNull();
    expect(parseIssueId(null)).toBeNull();
  });
});

describe("parseStateCode", () => {
  it("accepts a real state code and uppercases it", () => {
    expect(parseStateCode("tx")).toBe("TX");
    expect(parseStateCode("CA")).toBe("CA");
  });

  it("accepts DC", () => {
    expect(parseStateCode("dc")).toBe("DC");
  });

  it("rejects anything that isn't exactly 2 letters", () => {
    expect(parseStateCode("texas")).toBeNull();
    expect(parseStateCode("t")).toBeNull();
    expect(parseStateCode("12")).toBeNull();
  });

  // The bug this guards against: a shape-only check (any 2 letters) let
  // a syntactically-valid but non-existent code through to render a
  // fully-formed, plausible-looking "ZZ Ballot 2026" share card —
  // asserting ZZ is a real jurisdiction rather than falling back to a
  // generic card the way an unknown issue/politician id already does.
  it("rejects a syntactically valid but non-existent state code", () => {
    expect(parseStateCode("zz")).toBeNull();
    expect(parseStateCode("xx")).toBeNull();
  });

  it("rejects garbage and empty input", () => {
    expect(parseStateCode("")).toBeNull();
    expect(parseStateCode(null)).toBeNull();
  });
});

function race(id: string, office: string): RaceWithCandidates {
  return {
    id, cycleYear: 2026, office, state: "GA", district: office === "H" ? 6 : null,
    isSpecial: false, pvi: null, pviLevel: null, candidateSource: "confirmed",
    counties: null, candidates: [],
  };
}

function ballot(senateRaces: RaceWithCandidates[], houseRaces: RaceWithCandidates[]): StateBallot {
  return {
    state: "GA", cycleYear: 2026, electionDate: "2026-11-03", electionType: "general",
    primaryDate: null, statePvi: null, senateRaces, nextSenateElection: null, houseRaces,
    coverage: [], measures: [],
    measureCoverage: { status: "not_yet_covered", sourceName: null, checkedAt: null },
    officialLookup: { url: "https://example.com", label: "", sourceName: "", isStateSpecific: false, verifiedAt: null },
    omits: [],
  };
}

describe("findRace", () => {
  const senate = race("2026-SEN-GA", "S");
  const house = race("2026-HOUSE-GA-6", "H");
  const b = ballot([senate], [house]);

  it("finds a Senate race by id", () => {
    expect(findRace(b, "2026-SEN-GA")).toBe(senate);
  });

  it("finds a House race by id", () => {
    expect(findRace(b, "2026-HOUSE-GA-6")).toBe(house);
  });

  // The bug this guards against: a post's ?race= id that's stale (the
  // race was merged/renumbered since the post went out) or mistyped must
  // fall back to the generic state card, never render a card that looks
  // race-specific while showing nothing for it.
  it("returns null for an id not on this ballot", () => {
    expect(findRace(b, "2026-HOUSE-GA-7")).toBeNull();
  });
});
