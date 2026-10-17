import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OnBallotLine, SeatRaceLine } from "./PoliticianProfileClient";

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

  it("adds the seat's usual vote from the map and links its place there, never a poll range", async () => {
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
    const link = await screen.findByRole("link", { name: /ON THE MAP.*USUAL VOTE R\+2/ });
    expect(link).toHaveAttribute(
      "href",
      "/elections/map?chamber=senate&view=usual&seat=2026-SEN-GA"
    );
    // The profile shows the member's Representation Score: no range beside it
    // (AGENTS.md, the Senate and House map, "Separate from scoring").
    expect(document.body).not.toHaveTextContent(/POLLS|D\+6 TO R\+3/i);
    expect(fetchMapSeats).toHaveBeenCalledWith({ seat: "2026-SEN-GA" });
  });
});

describe("SeatRaceLine", () => {
  const race = {
    raceId: "2026-HOUSE-KY-4",
    cycleYear: 2026,
    state: "KY",
    office: "H" as const,
    district: 4,
    redrawn: false,
    status: "notNominee" as const,
    certified: true,
    sourceName: "Kentucky Secretary of State (general election candidate filings)",
  };

  it("keeps a member who isn't a nominee one link from their seat's race, with the source", () => {
    render(<SeatRaceLine race={race} name="Pat Doe" />);
    expect(
      screen.getByText(/Pat Doe is not on the November ballot for this seat/)
    ).toBeInTheDocument();
    expect(screen.getByText(/Kentucky Secretary of State/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /WHO'S RUNNING FOR KY-4/ })).toHaveAttribute(
      "href",
      "/elections/states/KY#race-2026-HOUSE-KY-4"
    );
  });

  it("words primary results as primary results, and an unread state as unread", () => {
    const { rerender } = render(
      <SeatRaceLine race={{ ...race, certified: false }} name="Pat Doe" />
    );
    expect(
      screen.getByText(/not among the nominees Kentucky's primary results confirm/)
    ).toBeTruthy();
    rerender(<SeatRaceLine race={{ ...race, status: "unconfirmed" }} name="Pat Doe" />);
    expect(screen.getByText(/Confirmed nominees aren't available for Kentucky yet/)).toBeTruthy();
    expect(screen.queryByText(/Secretary of State/)).toBeNull();
    expect(screen.getByRole("link", { name: /WHO HAS FILED FOR KY-4/ })).toBeTruthy();
  });

  it("says a redrawn seat's number is a different district", () => {
    render(<SeatRaceLine race={{ ...race, redrawn: true }} name="Pat Doe" />);
    expect(screen.getByText(/NEW MAP/)).toBeTruthy();
    expect(screen.getByText(/is not the district\s+Pat Doe was elected in/)).toBeTruthy();
  });
});
