import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Senator, VotingRecord } from "@/types/senator";
import RepresentationScore from "./RepresentationScore";

vi.mock("@/components/shared/ScoreBreakdownPanel", () => ({ default: () => null }));

const votingRecord: VotingRecord = {
  totalVotes: 40, votedWithPartyCount: 8, votedAgainstPartyCount: 4,
  partyLoyaltyPct: 0, recentVoteCount: 0, keyVoteCount: 0,
};

function renderWith(votePart?: string) {
  const breakdown = {
    fundingIndependence: 50, promisePersistence: 50, constituentAlignment: 50,
    fundingDiversity: 50, legislativeEffectiveness: 50, overall: 50,
    confidence: votePart ? { constituentAlignment: "medium", constituentAlignmentVotePart: votePart } : undefined,
  } as Senator["representationScore"];
  return render(<RepresentationScore breakdown={breakdown} votingRecord={votingRecord} />);
}

describe("RepresentationScore constituent basis line", () => {
  it("states a neutral vote part for too few votes", () => {
    renderWith("neutral:few-votes");
    expect(screen.getByText(/too few party-line votes, vote part neutral 50/)).toBeInTheDocument();
  });

  it("states the party's typical score for too few votes when a party norm exists", () => {
    renderWith("typical:few-votes");
    expect(screen.getByText(/too few party-line votes, vote part set to the party's typical score/)).toBeInTheDocument();
  });

  it("states a neutral vote part when there is no party norm", () => {
    renderWith("neutral:no-expectation");
    expect(screen.getByText(/no party norm to compare with, vote part neutral 50/)).toBeInTheDocument();
  });

  it("states how much of the distance from the party's typical score a thin record keeps", () => {
    renderWith("shrunk:0.60");
    expect(screen.getByText(/vote part keeps 60% of its distance from the party's typical score/)).toBeInTheDocument();
  });

  it("says nothing extra at full confidence or for scores stored before the status existed", () => {
    renderWith("full");
    expect(screen.getByText("40 votes tracked")).toBeInTheDocument();
    renderWith(undefined);
    expect(screen.getAllByText("40 votes tracked")).toHaveLength(2);
  });
});
