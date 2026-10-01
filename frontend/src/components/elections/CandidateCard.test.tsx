import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import CandidateCard from "./CandidateCard";
import type { BallotCandidate } from "@/types/election";

function candidate(overrides: Partial<BallotCandidate>): BallotCandidate {
  return {
    id: "S6ME00316",
    name: "Calabrese, Carmem Vincent Mr.",
    party: "REP",
    confirmed: true,
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

describe("unconfirmed badge", () => {
  it("marks a candidate the state's primary file never listed", () => {
    render(<CandidateCard candidate={candidate({ confirmed: false })} showUnconfirmed />);
    expect(screen.getByText("UNCONFIRMED")).toBeInTheDocument();
  });

  it("stays off for a confirmed nominee", () => {
    render(<CandidateCard candidate={candidate({ confirmed: true })} showUnconfirmed />);
    expect(screen.queryByText("UNCONFIRMED")).not.toBeInTheDocument();
  });

  it("stays off when the whole race is unconfirmed, where it would be noise", () => {
    render(<CandidateCard candidate={candidate({ confirmed: false })} />);
    expect(screen.queryByText("UNCONFIRMED")).not.toBeInTheDocument();
  });
});

describe("incumbency badge", () => {
  it("names FEC's incumbency code on a seat whose lines did not change", () => {
    render(<CandidateCard candidate={candidate({ incumbentChallenge: "I" })} />);
    expect(screen.getByText("INCUMBENT")).toBeInTheDocument();
  });

  it("calls a sitting member on a redrawn seat a sitting member, and drops the old seat's other codes", () => {
    const { unmount } = render(
      <CandidateCard candidate={candidate({ incumbentChallenge: "I" })} redrawnSeat />
    );
    expect(screen.getByText("SITTING MEMBER")).toBeInTheDocument();
    expect(screen.queryByText("INCUMBENT")).not.toBeInTheDocument();
    unmount();
    render(<CandidateCard candidate={candidate({ incumbentChallenge: "C" })} redrawnSeat />);
    expect(screen.queryByText("CHALLENGER")).not.toBeInTheDocument();
  });

  it("calls them the member going in from election day on, with the seat they held", () => {
    render(
      <CandidateCard
        candidate={candidate({
          incumbentChallenge: "I",
          incumbentRecord: { id: "C001131", score: 50, district: 35, seat: "TX-35" },
        })}
        redrawnSeat
        resultsMode
      />
    );
    expect(screen.getByText("MEMBER GOING IN, TX-35")).toBeInTheDocument();
    expect(screen.queryByText(/SITTING MEMBER/)).not.toBeInTheDocument();
  });
});
