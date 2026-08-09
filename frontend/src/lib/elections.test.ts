import { describe, expect, it } from "vitest";
import {
  districtCountiesLabel,
  formatPvi,
  isActiveCandidate,
  parseUtc,
  raceShortLabel,
  raceTitleLabel,
} from "./elections";
import type { CandidateSummary } from "@/types/election";

describe("formatPvi", () => {
  it("formats R lean, D lean, even, and missing", () => {
    expect(formatPvi(3)).toBe("R+3");
    expect(formatPvi(-7)).toBe("D+7");
    expect(formatPvi(0)).toBe("EVEN");
    expect(formatPvi(null)).toBe("N/A");
  });
});

describe("race labels", () => {
  const senate = { office: "S", state: "GA", district: null };
  const house = { office: "H", state: "GA", district: 7 };
  // FEC codes at-large districts as "00" → district 0. The bug this
  // guards against: a truthy `race.district ?` check treated 0 like null.
  const atLarge = { office: "H", state: "AK", district: 0 };

  it("raceShortLabel renders card-style caps labels", () => {
    expect(raceShortLabel(senate)).toBe("GA SENATE");
    expect(raceShortLabel(house)).toBe("GA-7");
    expect(raceShortLabel(atLarge)).toBe("AK-AL");
  });

  it("raceTitleLabel renders title-style labels", () => {
    expect(raceTitleLabel(senate)).toBe("GA Senate");
    expect(raceTitleLabel(house)).toBe("GA-7 House");
    expect(raceTitleLabel(atLarge)).toBe("AK-AL House");
  });

  it("does not conflate a null district (Senate) with at-large 0", () => {
    expect(raceShortLabel({ office: "H", state: "TX", district: null })).toBe("TX HOUSE");
    expect(raceShortLabel({ office: "H", state: "TX", district: 0 })).toBe("TX-AL");
  });
});

describe("districtCountiesLabel", () => {
  it("returns null for null or empty input", () => {
    expect(districtCountiesLabel(null)).toBeNull();
    expect(districtCountiesLabel([])).toBeNull();
  });

  it("drops the generic County suffix but keeps Parish/Borough/city", () => {
    expect(districtCountiesLabel(["Fulton County", "Orleans Parish", "Denali Borough"])).toBe(
      "Fulton, Orleans Parish, Denali Borough"
    );
  });

  it("keeps a (part) tag attached to its county", () => {
    expect(districtCountiesLabel(["Fulton County (part)"])).toBe("Fulton (part)");
  });

  it("truncates long lists with a remainder count", () => {
    const counties = ["A County", "B County", "C County", "D County", "E County"];
    expect(districtCountiesLabel(counties, 3)).toBe("A, B, C & 2 more");
  });

  it("does not truncate when exactly at the max", () => {
    expect(districtCountiesLabel(["A County", "B County"], 3)).toBe("A, B");
  });
});

describe("parseUtc", () => {
  it("treats an offset-less timestamp as UTC", () => {
    expect(parseUtc("2026-07-04T12:00:00")?.toISOString()).toBe("2026-07-04T12:00:00.000Z");
  });

  it("leaves an explicit Z untouched", () => {
    expect(parseUtc("2026-07-04T12:00:00Z")?.toISOString()).toBe("2026-07-04T12:00:00.000Z");
    expect(parseUtc("2026-07-04T12:00:00.500Z")?.toISOString()).toBe("2026-07-04T12:00:00.500Z");
  });

  it("respects an explicit numeric offset", () => {
    expect(parseUtc("2026-07-04T12:00:00+05:00")?.toISOString()).toBe("2026-07-04T07:00:00.000Z");
    expect(parseUtc("2026-07-04T12:00:00-0500")?.toISOString()).toBe("2026-07-04T17:00:00.000Z");
  });

  it("parses a bare date (already UTC per spec) without appending Z", () => {
    expect(parseUtc("2026-07-04")?.toISOString()).toBe("2026-07-04T00:00:00.000Z");
  });

  it("returns null for garbage", () => {
    expect(parseUtc("not a date")).toBeNull();
    expect(parseUtc("")).toBeNull();
  });
});

describe("isActiveCandidate", () => {
  const base: CandidateSummary = {
    id: "H6GA07123",
    name: "Test Candidate",
    party: "DEM",
    incumbentChallenge: null,
    candidateStatus: null,
    hasRaisedFunds: false,
    contributions: null,
    cashOnHand: null,
    lastFinancialsSync: null,
  };

  it("counts statutory candidates, fundraisers, and incumbents as active", () => {
    expect(isActiveCandidate({ ...base, candidateStatus: "C" })).toBe(true);
    expect(isActiveCandidate({ ...base, hasRaisedFunds: true })).toBe(true);
    expect(isActiveCandidate({ ...base, incumbentChallenge: "I" })).toBe(true);
  });

  it("treats paper/prior-cycle filers as inactive", () => {
    expect(isActiveCandidate(base)).toBe(false);
    expect(isActiveCandidate({ ...base, candidateStatus: "P" })).toBe(false);
    expect(isActiveCandidate({ ...base, candidateStatus: "N", incumbentChallenge: "C" })).toBe(
      false
    );
  });
});
