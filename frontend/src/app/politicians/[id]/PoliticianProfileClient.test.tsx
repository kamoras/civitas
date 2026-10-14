import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OnBallotLine } from "./PoliticianProfileClient";

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
});
