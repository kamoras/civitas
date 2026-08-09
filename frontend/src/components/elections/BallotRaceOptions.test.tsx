import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import BallotRaceOptions from "./BallotRaceOptions";
import type { BallotCandidate, RaceWithCandidates } from "@/types/election";

function candidate(overrides: Partial<BallotCandidate>): BallotCandidate {
  return {
    id: "C1",
    name: "SMITH, JANE",
    party: "DEM",
    incumbentChallenge: null,
    candidateStatus: "C",
    hasRaisedFunds: true,
    contributions: null,
    cashOnHand: null,
    lastFinancialsSync: null,
    incumbentRecord: null,
    ...overrides,
  };
}

function race(candidates: BallotCandidate[]): RaceWithCandidates {
  return {
    id: "2026-SEN-TX",
    cycleYear: 2026,
    office: "S",
    state: "TX",
    district: null,
    isSpecial: false,
    pvi: 6,
    pviLevel: "state",
    counties: null,
    candidates,
  };
}

describe("BallotRaceOptions", () => {
  it("explains a negative cash-on-hand figure instead of leaving it looking like an error", () => {
    render(
      <BallotRaceOptions
        race={race([
          candidate({
            id: "R1",
            party: "REP",
            cashOnHand: -781.22,
            lastFinancialsSync: "2026-07-31T00:00:00Z",
          }),
        ])}
      />
    );
    const badge = screen.getByText("-$781 CASH");
    expect(badge.title).toMatch(/debts exceed its cash on hand/);
  });

  it("still shows a plain as-of tooltip for a positive cash-on-hand figure", () => {
    render(
      <BallotRaceOptions
        race={race([
          candidate({
            id: "R1",
            party: "REP",
            cashOnHand: 3_200_000,
            lastFinancialsSync: "2026-08-01T00:00:00Z",
          }),
        ])}
      />
    );
    const badge = screen.getByText("$3.2M CASH");
    expect(badge.title).toBe("Cash on hand as of 2026-08-01");
  });
});
