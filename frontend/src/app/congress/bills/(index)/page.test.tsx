import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import BillsPage from "./page";

const nav = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({ useSearchParams: () => nav.params }));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));
vi.mock("@/components/bills/BillStageFlow", () => ({
  default: () => null,
  ALL_STAGE_CODES: ["REFERRED", "REPORTED"],
}));
vi.mock("@/components/bills/BillRow", () => ({
  default: ({ bill }: { bill: { billId: string } }) => <p>{bill.billId}</p>,
}));
vi.mock("@/lib/api", () => ({
  fetchBillsInFlight: vi.fn(
    async (opts: { sort?: string; stage?: string; q?: string; reached?: string }) => ({
      bills: opts.reached
        ? [{ billId: `${opts.reached}-bill`, chamber: "senate", sponsorId: "x" }]
        : opts.sort === "hot"
          ? [{ billId: "S.12", chamber: "senate", sponsorId: "x" }]
          : opts.q && opts.stage
            ? [{ billId: `${opts.stage}-match`, chamber: "senate", sponsorId: "x" }]
            : [],
      total: opts.sort === "hot" || opts.reached ? 1 : 0,
      page: 1,
      perPage: 50,
      totalPages: 1,
      stageCounts: { REFERRED: 90, REPORTED: 7 },
      inMotion: 7,
      shown: opts.sort === "hot" ? "moved" : null,
      movedSince: opts.sort === "hot" ? "2026-10-03" : null,
      reachedLabel: opts.reached ? "Bills that became law this Congress" : null,
    })
  ),
}));

afterEach(() => {
  nav.params = new URLSearchParams();
  window.history.replaceState(null, "", "/");
});

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

  it("opens every stage a search's matches sit at, not only a lone one", async () => {
    render(<BillsPage />);
    await screen.findByText("S.12");
    fireEvent.change(screen.getByRole("textbox", { name: /bill number/ }), {
      target: { value: "student loans" },
    });
    expect(await screen.findByText("REFERRED-match")).toBeInTheDocument();
    expect(await screen.findByText("REPORTED-match")).toBeInTheDocument();
  });

  // Every toggle in a filter group says whether it is on (WCAG 4.1.2): the
  // party filter was the one group on the page without it.
  it("says which chamber and party filter is on", async () => {
    render(<BillsPage />);
    await userEvent.click(screen.getByRole("button", { name: "All Bills" }));
    for (const [group, on] of [
      ["Filter by chamber", "all"],
      ["Filter by sponsor's party", "All"],
    ]) {
      for (const button of within(screen.getByRole("group", { name: group })).getAllByRole(
        "button"
      ))
        expect(button).toHaveAttribute("aria-pressed", String(button.textContent === on));
    }
    const party = within(screen.getByRole("group", { name: "Filter by sponsor's party" }));
    await userEvent.click(party.getByRole("button", { name: "D" }));
    expect(party.getByRole("button", { name: "D" })).toHaveAttribute("aria-pressed", "true");
    expect(party.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "false");
  });

  // A /congress pipeline row opens ?reached=<milestone>: the bills that got
  // at least that far, under the row's own heading, and clearing it takes
  // the filter out of the address so a reload doesn't bring it back.
  it("lists a milestone's bills from ?reached= and clears it from the URL", async () => {
    nav.params = new URLSearchParams("reached=ENACTED");
    window.history.replaceState(null, "", "/congress/bills?reached=ENACTED");
    render(<BillsPage />);
    const heading = await screen.findByRole("heading", {
      name: "Bills that became law this Congress (1)",
    });
    expect(heading).toBeInTheDocument();
    expect(await screen.findByText("ENACTED-bill")).toBeInTheDocument();
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: /Show every bill in motion/ }));
    expect(window.location.search).toBe("");
    expect(screen.queryByRole("heading", { name: /became law/ })).not.toBeInTheDocument();
  });
});
