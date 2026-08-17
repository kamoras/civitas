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

function race(
  candidates: BallotCandidate[],
  candidateSource: RaceWithCandidates["candidateSource"] = "confirmed",
): RaceWithCandidates {
  return {
    id: "2026-SEN-TX",
    candidateSource,
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

  it("says when a list is raw FEC filers rather than real ballot options", () => {
    // Three quite different lists get shown in the same shape; a reader
    // can't tell a state-confirmed nominee from someone who merely filed
    // paperwork unless the page says so.
    render(<BallotRaceOptions race={race([candidate({ name: "SOMEONE, A" })], "filers")} />);
    expect(screen.getByText(/never appear on a ballot/i)).toBeInTheDocument();
  });

  it("says when a list is a primary ballot, not a decided nomination", () => {
    render(<BallotRaceOptions race={race([candidate({ name: "SOMEONE, A" })], "primary")} />);
    expect(screen.getByText(/aren't decided until the primary/i)).toBeInTheDocument();
  });

  it("admits when a confirmed list can't include third-party candidates", () => {
    // Confirming nominees from primary results cannot see a Libertarian
    // who never ran in one — a short ballot presented as complete is
    // worse than a short ballot that says so.
    render(<BallotRaceOptions race={race([candidate({ name: "SOMEONE, A" })], "nominees")} />);
    expect(screen.getByText(/without running in a primary/i)).toBeInTheDocument();
  });
});
