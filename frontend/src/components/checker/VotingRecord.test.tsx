import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
    billName, billId: billName, date: "2026-03-01", vote: "Yea", policyArea: "Health", policyAreas: [],
    partyAlignmentWeight: 0, stance: "", description: "", partyLeaning: null, votedWithParty: true,
    voteCategory: "recent",
  };
}

function votes(filter: string, billName: string, overrides: Partial<PaginatedVotes> = {}): PaginatedVotes {
  return {
    votes: [vote(billName)], total: 40, page: 1, perPage: 15, totalPages: 3, category: "recent", filter,
    counts: { all: 40, yea: 30, nay: 10, againstParty: 0 },
    ...overrides,
  };
}

const record = {
  totalVotes: 40, votedWithPartyCount: 30, votedAgainstPartyCount: 10, partyLoyaltyPct: 75,
  recentVoteCount: 40, keyVoteCount: 0,
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
    await waitFor(() => expect(screen.getByRole("button", { name: /^YEA/ })).toHaveAttribute("aria-pressed", "true"));
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
});
