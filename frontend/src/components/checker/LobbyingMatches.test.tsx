import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { LobbiedBill, LobbyingMatch } from "@/types/senator";
import LobbyingMatches from "./LobbyingMatches";

function lobbied(overrides: Partial<LobbiedBill>): LobbiedBill {
  return {
    billId: "HR.1492", label: "H.R. 1492", billName: "", vote: "Yea", motionType: "passage",
    filingYear: 2025, filingUrl: "https://lda.gov/f/1/print/", registrant: "ALTRIUS GROUP, LLC",
    client: "PFIZER INC.", filedBy: "ALTRIUS GROUP, LLC", filingCount: 1, ...overrides,
  };
}

function match(bills: LobbiedBill[], topical: string[] = []): LobbyingMatch {
  return {
    lobbyistOrg: "Pfizer Inc PAC", industry: "PHARMA", lobbyingSpend: 0, donationToSenator: 5000,
    billsInfluenced: topical, senatorVoteAligned: null, description: "d", lobbiedBills: bills,
  };
}

describe("LobbyingMatches", () => {
  it("names the client a filing was for, and who filed it when that adds something", () => {
    render(<LobbyingMatches matches={[match([lobbied({})])]} />);
    expect(screen.getByText(/2025 filing for PFIZER INC\. by ALTRIUS GROUP, LLC/)).toBeTruthy();
  });

  it("shows no motion label for a passage vote and a label for anything else", () => {
    render(
      <LobbyingMatches
        matches={[
          match([
            lobbied({}),
            lobbied({ billId: "S.1040", label: "S. 1040", vote: "Nay", motionType: "veto", client: "X", filedBy: null }),
            lobbied({ billId: "S.1041", label: "S. 1041", vote: "Nay", motionType: null, client: "Y", filedBy: null }),
          ]),
        ]}
      />
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toMatch(/voted Yea ·/);
    expect(items[0]).not.toMatch(/\(on /);
    expect(items[1]).toMatch(/voted Nay \(on a motion about the President's veto\)/);
    // An unrecorded motion must not read as the vote on the bill.
    expect(items[2]).toMatch(/voted Nay \(on a motion, not necessarily passage\)/);
  });

  it("lists topically related bills only when there are any", () => {
    render(<LobbyingMatches matches={[match([lobbied({})])]} />);
    expect(screen.queryByText(/TOPICALLY RELATED BILLS/)).toBeNull();
  });
});
