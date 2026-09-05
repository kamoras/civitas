import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import RaceFinancials from "./RaceFinancials";
import type { CandidateSummary } from "@/types/election";

function candidate(overrides: Partial<CandidateSummary>): CandidateSummary {
  return {
    id: "id",
    name: "name",
    party: "DEM",
    incumbentChallenge: null,
    candidateStatus: "C",
    hasRaisedFunds: true,
    contributions: null,
    cashOnHand: null,
    lastFinancialsSync: null,
    ...overrides,
  };
}

describe("RaceFinancials", () => {
  it("excludes a candidate in debt from the bar chart entirely", () => {
    // A negative cashOnHand divided into `max` produces a negative CSS
    // width, silently clamped to 0 by the browser with no indication why
    // — this candidate's real bar chart context is "not comparable to
    // who has runway", not a zero-width bar next to everyone else's.
    render(
      <RaceFinancials
        candidates={[
          candidate({ id: "a", name: "Has Cash", cashOnHand: 1000 }),
          candidate({ id: "b", name: "In Debt", cashOnHand: -3500 }),
        ]}
      />
    );
    expect(screen.getByText("Has Cash")).toBeInTheDocument();
    expect(screen.queryByText("In Debt")).not.toBeInTheDocument();
  });

  it("still renders the chart from the remaining non-negative candidates", () => {
    render(
      <RaceFinancials
        candidates={[
          candidate({ id: "a", name: "Has Cash", cashOnHand: 1000 }),
          candidate({ id: "b", name: "In Debt", cashOnHand: -3500 }),
        ]}
      />
    );
    expect(screen.getByText("Cash on hand")).toBeInTheDocument();
    expect(screen.queryByText(/No fundraising data synced/)).not.toBeInTheDocument();
  });

  it("falls back to the no-data message when every candidate is null or in debt", () => {
    render(
      <RaceFinancials
        candidates={[
          candidate({ id: "a", name: "Never Synced", cashOnHand: null }),
          candidate({ id: "b", name: "In Debt", cashOnHand: -100 }),
        ]}
      />
    );
    expect(screen.getByText(/No fundraising data synced/)).toBeInTheDocument();
  });
});
