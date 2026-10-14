import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import BillsPage from "./page";

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));
vi.mock("@/components/bills/BillStageFlow", () => ({
  default: () => null,
  ALL_STAGE_CODES: ["REPORTED"],
}));
vi.mock("@/components/bills/BillRow", () => ({
  default: ({ bill }: { bill: { billId: string } }) => <p>{bill.billId}</p>,
}));
vi.mock("@/lib/api", () => ({
  fetchBillsInFlight: vi.fn(async (opts: { sort?: string }) => ({
    bills: opts.sort === "hot" ? [{ billId: "S.12", chamber: "senate", sponsorId: "x" }] : [],
    total: opts.sort === "hot" ? 1 : 0,
    page: 1,
    perPage: 50,
    totalPages: 1,
    stageCounts: { REFERRED: 90, REPORTED: 7 },
    inMotion: 7,
    shown: opts.sort === "hot" ? "moved" : null,
    movedSince: opts.sort === "hot" ? "2026-10-03" : null,
  })),
}));

describe("bills page", () => {
  it("says the Active now list is bills that moved this week when no issue names one", async () => {
    render(<BillsPage />);
    expect(await screen.findByText("S.12")).toBeInTheDocument();
    expect(
      screen.getByText(
        /No Action Center issue names a bill right now, so this lists bills that moved this week/
      )
    ).toHaveTextContent("Saturday, October 3");
    // The headline count is the backend's, not a sum worked out here.
    expect(await screen.findByText(/Where 7 bills sit/)).toBeInTheDocument();
  });

  it("searches every bill once a reader types, not just the Active now list", async () => {
    render(<BillsPage />);
    await screen.findByText("S.12");
    fireEvent.change(screen.getByRole("textbox", { name: /bill number/ }), {
      target: { value: "S. 2398" },
    });
    expect(screen.getByRole("button", { name: "All Bills" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.getByRole("button", { name: "D" })).toHaveAttribute("aria-pressed", "false");
  });
});
