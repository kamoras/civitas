import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import RaceFullDetail from "./RaceFullDetail";
import type { RaceWithCandidates } from "@/types/election";

vi.mock("./CandidateCard", () => ({
  default: ({ candidate }: { candidate: { name: string } }) => <div>{candidate.name}</div>,
}));
vi.mock("./RaceFinancials", () => ({ default: () => <div data-testid="financials" /> }));

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
    candidateSource: "filers",
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
      />
    );

    expect(screen.getByText("Active Ann")).toBeInTheDocument();
    // <details> content is present but collapsed, not absent — jsdom
    // doesn't apply the closed-<details> UA style that hides it visually.
    expect(screen.getByText("Paper Pete").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Other FEC filers (1)")).toBeInTheDocument();
    expect(screen.getByText(/Everyone who has filed with the FEC/)).toBeInTheDocument();
  });

  it("shows a fallback message with no candidates and no fundraising section", () => {
    render(<RaceFullDetail race={race()} />);

    expect(screen.getByText(/No candidates on record/)).toBeInTheDocument();
    expect(screen.queryByTestId("financials")).not.toBeInTheDocument();
    // The candidateSource note ("Everyone who has filed with the FEC...")
    // must not render alongside "no candidates on record" — the two
    // together would tell a reader both that a ballot answer exists AND
    // that there's nobody on record for it.
    expect(screen.queryByText(/Everyone who has filed with the FEC/)).not.toBeInTheDocument();
  });

  it("points to this race's badge in the page-level coverage feed instead of repeating it", () => {
    render(<RaceFullDetail race={race({ office: "H", district: 6 })} />);

    expect(screen.getByText(/News coverage of this race is tagged/)).toBeInTheDocument();
    expect(screen.getByText("HOUSE-6")).toBeInTheDocument();
  });
});
