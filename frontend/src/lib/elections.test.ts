import { describe, expect, it } from "vitest";
import {
  districtCountiesLabel,
  formatPvi,
  isActiveCandidate,
  parseUtc,
  raceBadgeLabel,
  raceShortLabel,
  raceTitleLabel,
  tierCandidates,
} from "./elections";
import type { BallotCandidate, CandidateSummary } from "@/types/election";

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

  it("raceBadgeLabel omits the state for a page already scoped to one", () => {
    expect(raceBadgeLabel(senate)).toBe("SENATE");
    expect(raceBadgeLabel(house)).toBe("HOUSE-7");
    expect(raceBadgeLabel(atLarge)).toBe("HOUSE-AL");
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

describe("tierCandidates", () => {
  // Real Ohio 2026 Senate primary field, live-verified 2026-09-04 —
  // 9 real active FEC filers behind one real matchup (Brown vs Husted).
  function cand(overrides: Partial<BallotCandidate>): BallotCandidate {
    return {
      id: "id",
      name: "name",
      party: "DEM",
      incumbentChallenge: "C",
      candidateStatus: "C",
      hasRaisedFunds: true,
      contributions: null,
      cashOnHand: null,
      lastFinancialsSync: "2026-08-25T00:00:00Z",
      incumbentRecord: null,
      ...overrides,
    };
  }

  const brown = cand({ id: "S6OH00163", name: "Brown, Sherrod", party: "DEM", cashOnHand: 16229741.32 });
  const husted = cand({
    id: "S6OH00304", name: "Husted, Jon", party: "REP", incumbentChallenge: "I", cashOnHand: 9419311.2,
  });
  const levy = cand({ id: "S6OH00395", name: "Levy, Gregory Lee", party: "IND", cashOnHand: 25971.13 });
  const ode = cand({ id: "S6OH00387", name: "Ode, Frederick J", party: "DEM", cashOnHand: 25865.53 });
  const kincaid = cand({ id: "S6OH00361", name: "Kincaid, Ronald E Jr", party: "DEM", cashOnHand: 17486.87 });
  const redpath = cand({ id: "S6OH00429", name: "Redpath, William", party: "LIB", cashOnHand: 730.35 });
  const volpe = cand({ id: "S6OH00353", name: "Volpe, Christopher", party: "DEM", cashOnHand: 168.3 });
  const faris = cand({ id: "S6OH00243", name: "Faris, Stephen I Mr", party: "IND", cashOnHand: 155.94 });

  const field = [brown, husted, levy, ode, kincaid, redpath, volpe, faris];

  it("makes the real top-fundraiser-per-major-party the leaders", () => {
    const { leaders } = tierCandidates(field);
    expect(leaders.map((c) => c.id).sort()).toEqual([brown.id, husted.id].sort());
  });

  it("keeps every minor-party filer in the tail when none clears 10% of the leaders' cash", () => {
    // Levy's real $26K is ~0.16% of Brown's $16.2M -- nowhere close to
    // viable by this rule, so all six non-major candidates recede.
    const { tail } = tierCandidates(field);
    expect(tail.map((c) => c.id).sort()).toEqual(
      [levy.id, ode.id, kincaid.id, redpath.id, volpe.id, faris.id].sort()
    );
  });

  it("promotes a genuinely viable third-party candidate into the leader row", () => {
    const viableIndependent = cand({ id: "IND1", party: "IND", cashOnHand: 2000000 });
    const { leaders } = tierCandidates([brown, husted, viableIndependent]);
    expect(leaders.map((c) => c.id)).toContain(viableIndependent.id);
  });

  it("always includes an incumbent as a leader regardless of party or cash", () => {
    const brokeIncumbent = cand({ id: "INC1", party: "REP", incumbentChallenge: "I", cashOnHand: 100 });
    const { leaders, tail } = tierCandidates([brown, brokeIncumbent]);
    expect(leaders.map((c) => c.id)).toContain(brokeIncumbent.id);
    expect(tail.map((c) => c.id)).not.toContain(brokeIncumbent.id);
  });

  it("shows fewer leader cards when one major party has no candidate, rather than inventing one", () => {
    const { leaders } = tierCandidates([brown, levy, ode]);
    expect(leaders).toEqual([brown]);
  });

  it("never tiers an inactive (paper-filer) candidate into either bucket incorrectly", () => {
    const inactive = cand({
      id: "PAPER1", party: "REP", candidateStatus: "P", hasRaisedFunds: false, incumbentChallenge: null,
    });
    const { leaders, tail } = tierCandidates([brown, inactive]);
    expect(leaders.map((c) => c.id)).not.toContain(inactive.id);
    expect(tail.map((c) => c.id)).not.toContain(inactive.id);
  });
});
