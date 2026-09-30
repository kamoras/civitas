import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { HouseResultRow, RaceResultCard, statusTag } from "./RaceResult";
import type { LiveRaceResult } from "@/types/election";

afterEach(cleanup);

function race(overrides: Partial<LiveRaceResult> = {}): LiveRaceResult {
  return {
    raceId: "2026-HOUSE-GA-2",
    state: "GA",
    office: "H",
    district: 2,
    isSpecial: false,
    heldBy: "DEM",
    official: false,
    votesCounted: 1900,
    reportingUnits: 80,
    totalUnits: 100,
    unitLabel: "precincts",
    sourceName: "GA SOS",
    sourceUrl: null,
    fetchedAt: "2026-11-04T02:44:00Z",
    lastChangeAt: "2026-11-04T02:42:00Z",
    leaderParty: "REP",
    flip: false,
    candidates: [
      { name: "Ray Jones", party: "REP", votes: 1000, pct: 52.6, candidateId: null },
      { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
    ],
    ...overrides,
  };
}

const TIE = race({
  leaderParty: null,
  candidates: [
    { name: "Ray Jones", party: "REP", votes: 950, pct: 50, candidateId: null },
    { name: "Dana Smith", party: "DEM", votes: 950, pct: 50, candidateId: null },
  ],
});

describe("statusTag", () => {
  it("tags an exact tie as tied, not leading", () => {
    expect(statusTag(TIE).text).toBe("TIED");
    expect(statusTag(race()).text).toBe("LEADING");
    expect(statusTag(race({ votesCounted: 0 })).text).toBe("NO VOTES YET");
    expect(statusTag({ ...TIE, official: true }).text).toBe("OFFICIAL");
  });
});

describe("HouseResultRow", () => {
  it("names both tied candidates without colouring either as ahead", () => {
    render(
      <ol>
        <HouseResultRow result={TIE} />
      </ol>
    );
    const row = screen.getByRole("listitem");
    expect(row).toHaveTextContent("Tied: Ray Jones (R) 50.0% · Dana Smith (D) 50.0%");
    expect(row.querySelector(".text-rep-red")).toBeNull();
    expect(row).toHaveTextContent("TIED");
  });

  it("lands a #race- link clear of the fixed header, and can take focus", () => {
    render(
      <ol>
        <HouseResultRow result={race()} />
      </ol>
    );
    const row = screen.getByRole("listitem");
    expect(row).toHaveClass("scroll-mt-[var(--header-clearance)]");
    expect(row).toHaveAttribute("tabindex", "-1");
  });
});

describe("RaceResultCard", () => {
  it("lands a #race- link clear of the fixed header", () => {
    render(<RaceResultCard result={race({ office: "S", district: null })} />);
    expect(screen.getByRole("article")).toHaveClass("scroll-mt-[var(--header-clearance)]");
  });

  it("says a tie is tied, not led", () => {
    render(<RaceResultCard result={TIE} />);
    expect(screen.getByText(/Tied, not called/)).toBeInTheDocument();
    expect(screen.queryByText(/Leading, not called/)).not.toBeInTheDocument();
  });

  it("says a House seat on new district lines has no previous holder", () => {
    render(<RaceResultCard result={race({ state: "TX", heldBy: null })} newLines />);
    expect(screen.getByText(/new district lines · no previous holder/)).toBeInTheDocument();
    expect(screen.queryByText(/held by/)).not.toBeInTheDocument();
  });

  it("says nothing of new lines for a seat with a holder, or a Senate race", () => {
    render(<RaceResultCard result={race({ heldBy: "DEM" })} newLines />);
    expect(screen.getByText(/held by D/)).toBeInTheDocument();
    cleanup();
    render(
      <RaceResultCard result={race({ office: "S", district: null, heldBy: null })} newLines />
    );
    expect(screen.queryByText(/no previous holder/)).not.toBeInTheDocument();
    cleanup();
    // Unknown holder in a state whose lines did not change: no claim either way.
    render(<RaceResultCard result={race({ heldBy: null })} />);
    expect(screen.queryByText(/no previous holder|held by/)).not.toBeInTheDocument();
  });
});

describe("the count's own words", () => {
  it("says no votes are counted yet on a card with none, not 'leading'", () => {
    render(<RaceResultCard result={race({ office: "S", district: null, votesCounted: 0 })} />);
    expect(screen.getByText("NO VOTES YET")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("No votes counted yet.");
    expect(document.body).not.toHaveTextContent(/Leading, not called/);
  });

  it("gives a House row its reporting figure on a phone too", () => {
    render(
      <ol>
        <HouseResultRow result={race()} />
      </ol>
    );
    // One copy for phones (sm:hidden), one for wider screens (hidden
    // sm:block): neither breakpoint drops it.
    const shown = screen.getAllByText("80 of 100 precincts reporting (80%)");
    expect(shown.map((el) => el.className)).toEqual(
      expect.arrayContaining([
        expect.stringContaining("sm:hidden"),
        expect.stringContaining("sm:block"),
      ])
    );
    expect(shown.find((el) => el.className.includes("sm:hidden"))!.className).not.toMatch(
      /(^|\s)hidden(\s|$)/
    );
  });
});
