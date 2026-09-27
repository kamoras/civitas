import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SponsoredBill } from "@/types/senator";
import SponsoredBills from "./SponsoredBills";

function bill(overrides: Partial<SponsoredBill>): SponsoredBill {
  return {
    billId: "H.R.1", title: "A bill", introducedDate: "2025-03-01", latestAction: "",
    latestActionDate: "", policyArea: "", policyAreas: [], partyLeaning: null,
    congress: 119, billType: "hr", isLaw: false, stage: "INTRODUCED", ...overrides,
  };
}

describe("SponsoredBills", () => {
  it("marks the bills Legislative Effectiveness weighted 1x as commemorative", () => {
    render(
      <SponsoredBills
        bills={[
          bill({ billId: "H.R.10", title: "To designate the facility of the United States Postal Service", commemorative: true }),
          bill({ billId: "H.R.11", title: "Rural Broadband Access Act" }),
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /SPONSORED LEGISLATION/ }));
    expect(screen.getAllByText("COMMEMORATIVE · 1×")).toHaveLength(1);
  });

  it("does not count a bill that is only in committee as advancing", () => {
    render(
      <SponsoredBills
        bills={[
          bill({ billId: "H.R.12", stage: "IN_COMMITTEE" }),
          bill({ billId: "H.R.13", stage: "PASSED_CHAMBER" }),
        ]}
      />,
    );
    expect(screen.getByText(/2 bills · 1 advancing/)).toBeInTheDocument();
  });
});
