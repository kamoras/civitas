import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ElectionsTab from "./ElectionsTab";

const fetchElectionInfo = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchElectionInfo }));
vi.mock("@/components/elections/RaceMap", () => ({
  default: () => <div data-testid="race-map" />,
  FIPS_TO_STATE: { "13": "GA" },
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

describe("ElectionsTab state panel", () => {
  it("names the site's election year, not the browser's, in early January", async () => {
    // The results window can run to January 3: on January 2, 2027 the site
    // is still on 2026, and a browser-year guess said "UP IN 2028".
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2027-01-02T15:00:00Z"));
    try {
      fetchElectionInfo.mockResolvedValue({
        ...info("results", false),
        states: [
          {
            state: "GA",
            hasSenateRace: true,
            hasHouseRace: true,
            houseDistricts: 14,
            senators: [
              {
                id: "S001",
                name: "Jon Ossoff",
                party: "D",
                upForElection: true,
                yearsInOffice: 6,
                overallScore: 60,
              },
            ],
          },
        ],
      });
      render(<ElectionsTab />);
      fireEvent.click(await screen.findByRole("button", { name: "GA" }));
      expect(screen.getByText("UP IN 2026")).toBeInTheDocument();
      expect(screen.getByText("ALL UP IN 2026")).toBeInTheDocument();
      expect(screen.queryByText(/2028/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
