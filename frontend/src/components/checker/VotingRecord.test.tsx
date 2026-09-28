import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import VotingRecord from "./VotingRecord";
import type { KeyVote, PaginatedVotes } from "@/types/senator";

const fetchSenatorVotes = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchSenatorVotes: (...args: unknown[]) => fetchSenatorVotes(...args),
  fetchRepVotes: vi.fn(),
}));

function vote(billName: string): KeyVote {
  return {
    billName,
    billId: billName,
    date: "2026-03-01",
    vote: "Yea",
    policyArea: "Health",
    policyAreas: [],
    partyAlignmentWeight: 0,
    stance: "",
    description: "",
    partyLeaning: null,
    votedWithParty: true,
    voteCategory: "recent",
  };
}

function votes(
  filter: string,
  billName: string,
  overrides: Partial<PaginatedVotes> = {}
): PaginatedVotes {
  return {
    votes: [vote(billName)],
    total: 40,
    page: 1,
    perPage: 15,
    totalPages: 3,
    category: "recent",
    filter,
    counts: { all: 40, yea: 30, nay: 10, againstParty: 0 },
    ...overrides,
  };
}

const record = {
  totalVotes: 40,
  votedWithPartyCount: 30,
  votedAgainstPartyCount: 10,
  partyLoyaltyPct: 75,
  recentVoteCount: 40,
  keyVoteCount: 0,
};

async function openList() {
  render(<VotingRecord senatorId="S1" votingRecord={record} />);
  await userEvent.click(screen.getByRole("button", { name: /VOTING RECORD/ }));
  await screen.findByText("First bill");
}

beforeEach(() => fetchSenatorVotes.mockReset());

describe("VotingRecord", () => {
  it("a slow response to an earlier filter never replaces a later one", async () => {
    fetchSenatorVotes.mockResolvedValueOnce(votes("all", "First bill"));
    await openList();

    let resolveNay: (value: PaginatedVotes) => void = () => {};
    fetchSenatorVotes.mockReturnValueOnce(new Promise<PaginatedVotes>((r) => (resolveNay = r)));
    fetchSenatorVotes.mockResolvedValueOnce(votes("yea", "Yea bill"));
    await userEvent.click(screen.getByRole("button", { name: /^NAY/ }));
    await userEvent.click(screen.getByRole("button", { name: /^YEA/ }));
    await screen.findByText("Yea bill");

    resolveNay(votes("nay", "Nay bill"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^YEA/ })).toHaveAttribute("aria-pressed", "true")
    );
    expect(screen.queryByText("Nay bill")).not.toBeInTheDocument();
    expect(screen.getByText("Yea bill")).toBeInTheDocument();
  });

  it("a failed change keeps the votes on screen and the filter they were loaded with", async () => {
    fetchSenatorVotes.mockResolvedValueOnce(votes("all", "First bill"));
    await openList();

    fetchSenatorVotes.mockRejectedValueOnce(new Error("Failed to load votes: 503"));
    await userEvent.click(screen.getByRole("button", { name: /^NAY/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load votes: 503");
    expect(screen.getByText("First bill")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^ALL/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^NAY/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("disables the pager while a filter change is replacing its list", async () => {
    fetchSenatorVotes.mockResolvedValueOnce(votes("all", "First bill"));
    await openList();

    fetchSenatorVotes.mockReturnValueOnce(new Promise<PaginatedVotes>(() => {})); // never resolves
    await userEvent.click(screen.getByRole("button", { name: /^NAY/ }));

    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("shows each vote as one line linked to its bill, without the pipeline's working", async () => {
    const warPowers: KeyVote = {
      ...vote("H CON RES 38"),
      billId: "HouseRC-2026-201",
      vote: "Nay",
      votedWithParty: false,
      partyLeaning: "bipartisan",
      policyArea: "DEFENSE",
      policyAreas: [{ area: "DEFENSE", confidence: 0.8, party: "bipartisan" }],
      stance: "neutral",
      rollCall: {
        chamber: "house",
        congress: 119,
        session: 2,
        number: 201,
        date: "2026-06-04",
        question: "On Agreeing to the Resolution",
        result: "Failed",
        billId: "HCONRES.38",
        billLabel: "H.Con.Res. 38",
        title:
          "Directing the President pursuant to section 5(c) of the War Powers Resolution to remove United States Armed Forces from Lebanon",
        sourceUrl: "https://clerk.house.gov/evs/2026/roll201.xml",
        parties: [],
      },
    };
    fetchSenatorVotes.mockResolvedValueOnce({ ...votes("all", "unused"), votes: [warPowers] });
    render(<VotingRecord senatorId="S1" votingRecord={record} />);
    await userEvent.click(screen.getByRole("button", { name: /VOTING RECORD/ }));

    const link = await screen.findByRole("link", {
      name: /War Powers Resolution to remove United States Armed Forces from Lebanon/,
    });
    expect(link).toHaveAttribute("href", "/congress/bills/HCONRES.38?congress=119");
    expect(screen.getByText("On Agreeing to the Resolution · Jun 4, 2026")).toBeInTheDocument();
    expect(
      within(link.closest("li") as HTMLElement).getByText("AGAINST PARTY")
    ).toBeInTheDocument();
    for (const noise of ["BP", "DEFENSE", "HouseRC-2026-201", /STANCE/]) {
      expect(screen.queryByText(noise)).not.toBeInTheDocument();
    }
  });

  it("links a vote stored before its roll call was recorded to the site's bill page", async () => {
    const early: KeyVote = { ...vote("S.1071"), billId: "S.1071", rollCall: null };
    const nomination: KeyVote = { ...vote("PN12-3"), billId: "PN12-3", rollCall: null };
    fetchSenatorVotes.mockResolvedValueOnce({
      ...votes("all", "unused"),
      votes: [early, nomination],
    });
    render(<VotingRecord senatorId="S1" votingRecord={record} />);
    await userEvent.click(screen.getByRole("button", { name: /VOTING RECORD/ }));

    expect(await screen.findByRole("link", { name: "S.1071" })).toHaveAttribute(
      "href",
      "/congress/bills/S.1071"
    );
    // Not a bill: nothing to open, and never Congress.gov.
    expect(screen.queryByRole("link", { name: "PN12-3" })).not.toBeInTheDocument();
  });
});
