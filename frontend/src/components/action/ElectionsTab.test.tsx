import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ElectionsTab from "./ElectionsTab";

const fetchElectionInfo = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchElectionInfo }));
vi.mock("@/components/elections/RaceMap", () => ({
  default: () => <div data-testid="race-map" />,
  FIPS_TO_STATE: {},
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function info(phase: string, isElectionDay: boolean) {
  return {
    nextElection: {
      date: "2026-11-03",
      type: "Midterm General Election",
      year: 2026,
      daysUntil: isElectionDay ? 0 : -9,
      isElectionDay,
      isElectionSeason: true,
      phase,
    },
    senateSeatsUp: 35,
    houseSeatsUp: 435,
    states: [],
  };
}

describe("ElectionsTab header", () => {
  it("says RESULTS for the results phase, not that they are still coming in", async () => {
    fetchElectionInfo.mockResolvedValue(info("results", false));
    render(<ElectionsTab />);
    expect(await screen.findByText("RESULTS")).toBeInTheDocument();
    expect(screen.queryByText(/COMING IN/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "SEE THE COUNT →" })).toHaveAttribute(
      "href",
      "/elections"
    );
  });

  it("says ELECTION DAY on the day", async () => {
    fetchElectionInfo.mockResolvedValue(info("election_day", true));
    render(<ElectionsTab />);
    expect(await screen.findByText("ELECTION DAY")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "FOLLOW THE LIVE COUNT →" })).toBeInTheDocument();
  });
});
