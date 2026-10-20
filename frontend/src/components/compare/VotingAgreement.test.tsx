import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Agreement } from "@/types/compare";

const agreement: Agreement = {
  chamber: "senate",
  congress: 119,
  bothVoted: 4,
  agreed: 3,
  split: 1,
  agreementPct: 75,
  sentence:
    "Voted the same way on 3 of the 4 roll calls this Congress where both cast a Yea or a Nay (75%).",
  recentSplits: [
    {
      chamber: "senate",
      congress: 119,
      session: 2,
      number: 41,
      date: "2026-09-30",
      question: "On the Nomination PN1",
      title: "A nominee",
      result: "Nomination Confirmed",
      billId: null,
      billLabel: null,
      left: "Nay",
      right: "Yea",
    },
  ],
};

vi.mock("@/lib/api", () => ({ fetchCompareAgreement: vi.fn(async () => agreement) }));
import VotingAgreement from "./VotingAgreement";

afterEach(cleanup);

describe("VotingAgreement", () => {
  it("shows the backend's share and sentence and each member's vote on a split", async () => {
    render(
      <VotingAgreement
        chamber="senate"
        left={{ id: "pat-able", name: "Pat Able" }}
        right={{ id: "lee-baker", name: "Lee Baker" }}
      />
    );
    expect(await screen.findByText("75%")).toBeTruthy();
    expect(screen.getByText(agreement.sentence)).toBeTruthy();
    const link = screen.getByRole("link", { name: "A nominee" });
    // A vote on no bill (a nomination) opens its own page.
    expect(link.getAttribute("href")).toBe("/congress/votes/senate/119/2/41");
    expect(screen.getByText(/Pat Able:/).textContent).toContain("Nay");
  });
});
