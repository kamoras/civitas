import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import RaceFullDetail from "./RaceFullDetail";
import type { RaceWithCandidates } from "@/types/election";

vi.mock("./CandidateCard", () => ({
  default: ({ candidate }: { candidate: { name: string } }) => <div>{candidate.name}</div>,
}));
vi.mock("./RaceFinancials", () => ({ default: () => <div data-testid="financials" /> }));
vi.mock("./CoverageFeed", () => ({ default: () => <div data-testid="coverage-feed" /> }));

function candidate(overrides: Partial<RaceWithCandidates["candidates"][number]>) {
  return {
    id: "c1",
    name: "Jane Doe",
    party: "DEM",
    incumbentChallenge: null,
    candidateStatus: null,
    hasRaisedFunds: false,
    contributions: null,
    cashOnHand: null,
    lastFinancialsSync: null,
    incumbentRecord: null,
    ...overrides,
  };
}

function race(overrides: Partial<RaceWithCandidates> = {}): RaceWithCandidates {
  return {
    id: "2026-HOUSE-GA-06",
    cycleYear: 2026,
    office: "H",
    state: "GA",
    district: 6,
    isSpecial: false,
    pvi: null,
    pviLevel: null,
    counties: null,
    candidates: [],
    ...overrides,
  };
}

describe("RaceFullDetail", () => {
  it("shows active candidates directly and collapses non-active ones", () => {
    render(
      <RaceFullDetail
        race={race({
          candidates: [
            candidate({ id: "active", name: "Active Ann", hasRaisedFunds: true }),
            candidate({ id: "other", name: "Paper Pete" }),
          ],
        })}
        coverage={[]}
      />
    );

    expect(screen.getByText("Active Ann")).toBeInTheDocument();
    // <details> content is present but collapsed, not absent — jsdom
    // doesn't apply the closed-<details> UA style that hides it visually.
    expect(screen.getByText("Paper Pete").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Other FEC filers (1)")).toBeInTheDocument();
  });

  it("shows a fallback message with no candidates and no fundraising section", () => {
    render(<RaceFullDetail race={race()} coverage={[]} />);

    expect(screen.getByText(/No candidates on record/)).toBeInTheDocument();
    expect(screen.queryByTestId("financials")).not.toBeInTheDocument();
  });

  it("only renders the per-race coverage section when there is coverage to show", () => {
    const { rerender } = render(<RaceFullDetail race={race()} coverage={[]} />);
    expect(screen.queryByTestId("coverage-feed")).not.toBeInTheDocument();

    rerender(
      <RaceFullDetail
        race={race()}
        coverage={[
          {
            id: 1,
            sourceType: "news",
            sourceName: "Times",
            title: "t",
            url: "https://example.com",
            summary: "s",
            author: null,
            publishedAt: null,
          },
        ]}
      />
    );
    expect(screen.getByTestId("coverage-feed")).toBeInTheDocument();
  });
});
