import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NotablePartyBreaks from "./NotablePartyBreaks";
import type { KeyVote } from "@/types/senator";

const fetchRepVotes = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchRepVotes: (...args: unknown[]) => fetchRepVotes(...args),
  fetchSenatorVotes: vi.fn(),
}));

// Tim Burchett (R-TN), House roll call 277 of 2026: the motion to recommit
// H.R. 8800, where Republicans voted 2-215 and he voted Yea.
const recommit: KeyVote = {
  billName: "National Defense Authorization Act for Fiscal Year 2027", billId: "HouseRC-2026-277", date: "",
  vote: "Yea", policyArea: "DEFENSE", policyAreas: [], partyAlignmentWeight: 0, stance: "neutral",
  description: "", partyLeaning: "D", votedWithParty: false, voteCategory: "recent",
  rollCall: {
    chamber: "house", congress: 119, session: 2, number: 277, date: "2026-07-22", question: "On Motion to Recommit",
    title: "National Defense Authorization Act for Fiscal Year 2027", result: "Failed", billId: "HR.8800",
    billLabel: "H.R. 8800", sourceUrl: "https://clerk.house.gov/evs/2026/roll277.xml",
    parties: [
      { party: "R", yea: 2, nay: 215, present: 0, notVoting: 3 },
      { party: "D", yea: 211, nay: 0, present: 0, notVoting: 2 },
    ],
  },
};

beforeEach(() => fetchRepVotes.mockReset());

describe("NotablePartyBreaks", () => {
  it("lists every break, not only key votes, with how each party voted", async () => {
    fetchRepVotes.mockResolvedValue({ votes: [recommit] });
    render(<NotablePartyBreaks entityId="tim-burchett" entityType="house" votedAgainstPartyCount={6} />);
    await userEvent.click(screen.getByRole("button", { name: /VOTES AGAINST THEIR PARTY \(6\)/ }));

    expect(fetchRepVotes).toHaveBeenCalledWith("tim-burchett", expect.objectContaining({ category: "all", filter: "against-party" }));
    expect(await screen.findByText(/On Motion to Recommit/)).toBeInTheDocument();
    expect(screen.getByText("Republicans: 2 yea, 215 nay")).toBeInTheDocument();
    expect(screen.getByText("Democrats: 211 yea, 0 nay")).toBeInTheDocument();
    expect(screen.getByText("VOTED YEA")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /House roll call 277, 2026-07-22/ })).toHaveAttribute("href", "/congress/2026-07-22");
  });

  it("shows nothing for a member with no breaks", () => {
    const { container } = render(<NotablePartyBreaks entityId="x" entityType="house" votedAgainstPartyCount={0} />);
    expect(container).toBeEmptyDOMElement();
  });
});
