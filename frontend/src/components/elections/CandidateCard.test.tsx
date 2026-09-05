import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import CandidateCard from "./CandidateCard";
import type { BallotCandidate } from "@/types/election";

function candidate(overrides: Partial<BallotCandidate>): BallotCandidate {
  return {
    id: "S6ME00316",
    name: "Calabrese, Carmem Vincent Mr.",
    party: "REP",
    incumbentChallenge: "C",
    candidateStatus: "C",
    hasRaisedFunds: true,
    contributions: 17759.71,
    cashOnHand: 100,
    lastFinancialsSync: "2026-08-25T00:00:00Z",
    incumbentRecord: null,
    ...overrides,
  };
}

describe("CandidateCard", () => {
  it("labels a positive cash on hand as cash on hand", () => {
    render(<CandidateCard candidate={candidate({ cashOnHand: 100 })} />);
    expect(screen.getByText("Cash on hand")).toBeInTheDocument();
    expect(screen.getByText("$100")).toBeInTheDocument();
  });

  it("relabels a negative cash on hand as debt, shown as a positive amount", () => {
    // Real Maine 2026 Senate data: -$3,500 cash on hand (FEC debt
    // exceeding receipts), live-verified 2026-09-04.
    render(<CandidateCard candidate={candidate({ cashOnHand: -3500 })} />);
    expect(screen.getByText("Debt")).toBeInTheDocument();
    expect(screen.queryByText("Cash on hand")).not.toBeInTheDocument();
    expect(screen.getByText("$4K")).toBeInTheDocument();
    expect(screen.queryByText(/-\$/)).not.toBeInTheDocument();
  });

  it("shows an em dash rather than $0 for a never-synced figure", () => {
    render(<CandidateCard candidate={candidate({ cashOnHand: null, lastFinancialsSync: null })} />);
    expect(screen.getByText("AWAITING FEC SYNC")).toBeInTheDocument();
  });
});
