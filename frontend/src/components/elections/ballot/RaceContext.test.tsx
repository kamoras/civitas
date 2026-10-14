import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RaceContext, { leanCountsSentence, leanSentence } from "./RaceContext";
import type { BallotCandidate, RaceWithCandidates } from "@/types/election";

function cand(over: Partial<BallotCandidate> = {}): BallotCandidate {
  return {
    id: "c1",
    name: "EXAMPLE, ONE",
    party: "DEM",
    confirmed: true,
    incumbentChallenge: null,
    hasRaisedFunds: true,
    candidateStatus: "C",
    contributions: 1,
    cashOnHand: 1,
    lastFinancialsSync: "2026-10-01T00:00:00Z",
    incumbentRecord: null,
    ...over,
  } as BallotCandidate;
}

function race(over: Partial<RaceWithCandidates> = {}): RaceWithCandidates {
  return {
    id: "2026-HOUSE-NC-1",
    cycleYear: 2026,
    office: "H",
    state: "NC",
    district: 1,
    isSpecial: false,
    pvi: 5,
    pviLevel: "district",
    leanContext: { unit: "districts", of: 435, moreDemocratic: 236, moreRepublican: 187, same: 11 },
    candidateSource: "confirmed",
    counties: null,
    candidates: [cand()],
    ...over,
  };
}

describe("RaceContext", () => {
  it("puts the lean into words and among every seat, one square each", () => {
    const { container } = render(<RaceContext race={race()} />);
    expect(screen.getByText("The seat · R+5")).toBeInTheDocument();
    expect(
      screen.getByRole("img", {
        name: "236 of 435 districts lean more Democratic, 187 more Republican, 11 the same.",
      })
    ).toBeInTheDocument();
    expect(container.querySelectorAll("[role=img] .aspect-square")).toHaveLength(435);
  });

  it("says plainly it is not a forecast, and offers the data", () => {
    render(<RaceContext race={race()} />);
    expect(screen.getByText(/publishes no prediction or odds/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "CSV: every federal candidate in North Carolina" })
    ).toHaveAttribute("href", "/api/elections/states/NC/races.csv");
    expect(screen.getByRole("link", { name: "JSON: this race" })).toHaveAttribute(
      "href",
      "/api/elections/races/2026-HOUSE-NC-1"
    );
  });

  it("leaves the lean out from election day, and calls no race", () => {
    render(<RaceContext race={race()} resultsMode />);
    expect(screen.queryByText(/The seat/)).not.toBeInTheDocument();
    expect(screen.getByText(/Civitas calls no race/)).toBeInTheDocument();
  });

  it("links a running incumbent's scorecard in one plain line", () => {
    render(
      <RaceContext
        race={race({
          candidates: [
            cand({
              ballotName: "One Example",
              incumbentChallenge: "I",
              incumbentRecord: { id: "member-a", score: 61.2 },
            }),
          ],
        })}
      />
    );
    expect(screen.getByText("INCUMBENT")).toBeInTheDocument();
    expect(screen.getByText(/One Example is on the ballot/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /SCORECARD/ })).toHaveAttribute(
      "href",
      "/politicians/member-a"
    );
  });

  it("has no seat row without a lean to place", () => {
    render(<RaceContext race={race({ pviLevel: "state", leanContext: null })} />);
    expect(screen.queryByText(/The seat/)).not.toBeInTheDocument();
  });
});

describe("lean wording", () => {
  it("reads each side and even", () => {
    expect(leanSentence(-1, "states")).toBe(
      "In the last two presidential elections, this state voted about 1 point more Democratic than the nation."
    );
    expect(leanSentence(0, "districts")).toMatch(/about the same as the nation/);
    expect(
      leanCountsSentence({
        unit: "states",
        of: 50,
        moreDemocratic: 20,
        moreRepublican: 29,
        same: 0,
      })
    ).toBe("20 of 50 states lean more Democratic, 29 more Republican.");
  });
});
