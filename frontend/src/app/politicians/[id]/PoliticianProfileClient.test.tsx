import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OnBallotLine } from "./PoliticianProfileClient";

const fetchMapSeats = vi.hoisted(() => vi.fn().mockResolvedValue(null));
vi.mock("@/lib/api", async (real) => ({ ...(await real<object>()), fetchMapSeats }));

describe("OnBallotLine", () => {
  it("links a member's own race on this election's ballot", () => {
    render(
      <OnBallotLine
        race={{
          raceId: "2026-HOUSE-TX-37",
          cycleYear: 2026,
          state: "TX",
          office: "H",
          district: 37,
        }}
      />
    );
    expect(screen.getByText(/2026 BALLOT/)).toBeInTheDocument();
    expect(screen.getByText(/U\.S\. HOUSE, DISTRICT 37, TEXAS/)).toBeInTheDocument();
    // The short name a phone shows in its place.
    expect(screen.getByText("TX-37")).toHaveClass("sm:hidden");
    expect(screen.getByRole("link", { name: /WHO'S RUNNING/ })).toHaveAttribute(
      "href",
      "/elections/states/TX#race-2026-HOUSE-TX-37"
    );
  });

  it("names another office and an at-large seat", () => {
    const { rerender } = render(
      <OnBallotLine
        race={{ raceId: "2026-SEN-GA", cycleYear: 2026, state: "GA", office: "S", district: null }}
      />
    );
    expect(screen.getByText(/U\.S\. SENATE, GEORGIA/)).toBeInTheDocument();
    rerender(
      <OnBallotLine
        race={{ raceId: "2026-HOUSE-WY-0", cycleYear: 2026, state: "WY", office: "H", district: 0 }}
      />
    );
    expect(screen.getByText(/U\.S\. HOUSE, AT-LARGE, WYOMING/)).toBeInTheDocument();
  });

  it("adds the seat's usual vote and poll range from the map, linking its place there", async () => {
    fetchMapSeats.mockResolvedValueOnce({
      cycle: 2026,
      asOf: "2026-10-16T12:00:00Z",
      stale: false,
      pollSource: { attribution: "Wikipedia contributors", license: "CC BY-SA 4.0", url: "" },
      seats: [
        {
          id: "2026-SEN-GA",
          chamber: "senate",
          state: "GA",
          district: null,
          special: false,
          leanLabel: "R+2",
          status: "range",
          range80Label: "D+6 to R+3",
          close: true,
          reason: null,
        },
      ],
    });
    render(
      <OnBallotLine
        race={{ raceId: "2026-SEN-GA", cycleYear: 2026, state: "GA", office: "S", district: null }}
      />
    );
    const link = await screen.findByRole("link", { name: /USUAL VOTE R\+2.*POLLS D\+6 TO R\+3/ });
    expect(link).toHaveAttribute(
      "href",
      "/elections/map?chamber=senate&view=polls&seat=2026-SEN-GA"
    );
    expect(fetchMapSeats).toHaveBeenCalledWith({ seat: "2026-SEN-GA" });
  });
});
