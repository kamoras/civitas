import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import StateBallotClient from "./StateBallotClient";
import type { RaceWithCandidates, StateBallot } from "@/types/election";

/** axe over the ballot page and every kind of research drawer. jsdom does
 * no layout, so colour contrast is left to the Lighthouse job; everything
 * structural — dialog and tab roles, names, labels, ids that aria points
 * at — is checked here, on every CI run. */

vi.mock("@/lib/api", () => ({
  fetchTownsForState: vi.fn().mockResolvedValue([]),
  fetchTownBallot: vi.fn(),
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));
vi.mock("@/components/elections/DistrictMap", () => ({ default: () => null }));

function candidate(
  id: string,
  name: string,
  party: string,
  extra: Partial<RaceWithCandidates["candidates"][number]> = {}
) {
  return {
    id,
    name,
    party,
    confirmed: true,
    incumbentChallenge: null,
    candidateStatus: "C",
    hasRaisedFunds: true,
    contributions: 1000,
    cashOnHand: 500,
    lastFinancialsSync: "2026-09-20T00:00:00Z",
    incumbentRecord: null,
    ...extra,
  };
}

function race(id: string, office: "S" | "H", district: number | null): RaceWithCandidates {
  return {
    id,
    cycleYear: 2026,
    office,
    state: "NC",
    district,
    isSpecial: false,
    pvi: 2,
    pviLevel: "district",
    candidateSource: "confirmed",
    counties: office === "H" ? ["Wake County (part)"] : null,
    candidates: [
      candidate(`${id}-d`, "A Democrat", "DEM", {
        incumbentRecord: { id: "M000001", score: 70.5 },
      }),
      candidate(`${id}-r`, "A Republican", "REP"),
    ],
  };
}

const ballot: StateBallot = {
  state: "NC",
  stateName: "North Carolina",
  cycleYear: 2026,
  electionDate: "2026-11-03",
  electionType: "general",
  primaryDate: "2026-03-03",
  statePvi: 2,
  nextSenateElection: null,
  senateRaces: [race("2026-SEN-NC", "S", null)],
  houseRaces: [race("2026-HOUSE-NC-1", "H", 1), race("2026-HOUSE-NC-2", "H", 2)],
  coverage: [
    {
      id: 1,
      sourceType: "news",
      sourceName: "A Paper",
      title: "A story",
      url: "https://example.org/a",
      summary: "What happened.",
      author: null,
      publishedAt: "2026-09-20T00:00:00Z",
      race: { id: "2026-SEN-NC", office: "S", district: null },
    },
  ],
  measures: [],
  measureCoverage: {
    status: "confirmed_none",
    sourceName: "NC SBE",
    checkedAt: null,
    lastAttemptAt: null,
  },
  statewideRaces: [],
  statewideCoverage: { status: "not_yet_covered", sourceName: null, checkedAt: null },
  stateLegRaces: [],
  judicialRaces: [],
  judicialCoverage: { status: "not_yet_covered", checkedAt: null, sourceName: null },
  officialLookup: {
    url: "https://www.usa.gov/election-office",
    label: "Find your election office",
    sourceName: "USA.gov",
    isStateSpecific: false,
    verifiedAt: null,
  },
  omits: ["County and municipal offices"],
};

async function violations() {
  const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
}

describe("ballot page accessibility", () => {
  it("really runs: a button with no name is caught", async () => {
    // Guards the tests below against passing vacuously.
    render(<button type="button" />);
    expect((await violations()).some((v) => v.startsWith("button-name"))).toBe(true);
  });

  it("has no axe violations with no drawer open", async () => {
    render(<StateBallotClient ballot={ballot} />);
    expect(await violations()).toEqual([]);
  });

  it("has none with a race's research drawer open, on every tab", async () => {
    render(<StateBallotClient ballot={ballot} />);
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    await userEvent.click(within(index).getByRole("button", { name: /U\.S\. Senator/ }));
    const drawer = within(screen.getByRole("dialog"));
    for (const tab of ["Money", "Record", "News 1"]) {
      await userEvent.click(drawer.getByRole("tab", { name: tab }));
      expect(await violations()).toEqual([]);
    }
  });

  it("has none in the House district picker and a picked district", async () => {
    render(<StateBallotClient ballot={ballot} />);
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    await userEvent.click(within(index).getByRole("button", { name: /U\.S\. Representative/ }));
    expect(await violations()).toEqual([]);
    const box = screen.getByTestId("ballot-columns");
    await userEvent.keyboard("{Escape}");
    await userEvent.click(within(box).getByRole("button", { name: "District 2" }));
    expect(await violations()).toEqual([]);
  });
});
