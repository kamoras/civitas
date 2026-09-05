import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RaceFullDetail from "./RaceFullDetail";
import type { RaceWithCandidates } from "@/types/election";

vi.mock("./CandidateCard", () => ({
  default: ({ candidate }: { candidate: { name: string } }) => <div>{candidate.name}</div>,
  getPartyMeta: () => ({ label: "", color: "", rule: "bg-ink-min" }),
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
    expect(screen.getByText(/every FEC filer/)).toBeInTheDocument();
  });

  it("shows a fallback message with no candidates and no fundraising section", () => {
    render(<RaceFullDetail race={race()} />);

    expect(screen.getByText(/No candidates on record/)).toBeInTheDocument();
    expect(screen.queryByTestId("financials")).not.toBeInTheDocument();
    // The candidateSource note ("...every FEC filer") must not render
    // alongside "no candidates on record" — the two together would tell
    // a reader both that a ballot answer exists AND that there's nobody
    // on record for it.
    expect(screen.queryByText(/every FEC filer/)).not.toBeInTheDocument();
  });

  it("points to this race's badge in the page-level coverage feed instead of repeating it", () => {
    render(<RaceFullDetail race={race({ office: "H", district: 6 })} />);

    expect(screen.getByText(/News coverage of this race is tagged/)).toBeInTheDocument();
    expect(screen.getByText("HOUSE-6")).toBeInTheDocument();
  });

  it("tiers an unconfirmed race into leader cards plus a collapsed tail", () => {
    render(
      <RaceFullDetail
        race={race({
          candidateSource: "filers",
          candidates: [
            candidate({ id: "d", name: "Big Dem", party: "DEM", hasRaisedFunds: true, cashOnHand: 1_000_000 }),
            candidate({ id: "r", name: "Big Rep", party: "REP", hasRaisedFunds: true, cashOnHand: 800_000 }),
            candidate({ id: "long-shot", name: "Long Shot", party: "DEM", hasRaisedFunds: true, cashOnHand: 50 }),
          ],
        })}
      />
    );

    expect(screen.getByText("Big Dem")).toBeInTheDocument();
    expect(screen.getByText("Big Rep")).toBeInTheDocument();
    // Long Shot is real content but recedes into the collapsed tail
    // rather than getting the same full-card treatment as the two
    // actual contenders — this is the whole point of tiering.
    expect(screen.queryByText("Long Shot")).not.toBeInTheDocument();
    expect(screen.getByText("1 more filed")).toBeInTheDocument();
    expect(screen.getByText("FEC-filed field")).toBeInTheDocument();
  });

  it("expands the tail on click to reveal who's in it", async () => {
    render(
      <RaceFullDetail
        race={race({
          candidateSource: "filers",
          candidates: [
            candidate({ id: "d", name: "Big Dem", party: "DEM", hasRaisedFunds: true, cashOnHand: 1_000_000 }),
            candidate({ id: "long-shot", name: "Long Shot", party: "DEM", hasRaisedFunds: true, cashOnHand: 50 }),
          ],
        })}
      />
    );

    expect(screen.queryByText("Long Shot")).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("1 more filed"));
    expect(screen.getByText("Long Shot")).toBeInTheDocument();
  });

  it("does not tier an already-confirmed race — every candidate gets a full card", () => {
    render(
      <RaceFullDetail
        race={race({
          candidateSource: "confirmed",
          candidates: [
            candidate({ id: "d", name: "Nominee One", party: "DEM", hasRaisedFunds: true, cashOnHand: 100 }),
            candidate({ id: "r", name: "Nominee Two", party: "REP", hasRaisedFunds: true, cashOnHand: 90 }),
          ],
        })}
      />
    );

    expect(screen.getByText("Nominee One")).toBeInTheDocument();
    expect(screen.getByText("Nominee Two")).toBeInTheDocument();
    expect(screen.queryByText("FEC-filed field")).not.toBeInTheDocument();
    expect(screen.queryByText(/more filed/)).not.toBeInTheDocument();
  });
});
