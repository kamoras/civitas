import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import ArchiveLead from "./ArchiveLead";
import type { ArchiveIndex } from "@/types/electionArchive";
import type { Season } from "@/types/specials";

const SEASON: Season = {
  season: "post_certification",
  betweenCycles: true,
  cycle: 2028,
  electionDay: "2028-11-07",
  heldCycle: 2026,
  until: "2027-01-02",
  firstPrimary: null,
  lastPrimary: null,
  lead: [],
};

describe("ArchiveLead", () => {
  it("never leads with a bare zero when no count was read", () => {
    const archive = {
      current: { cycle: 2028, electionDay: "2028-11-07", phase: "campaign" },
      cycles: [
        {
          cycle: 2026,
          electionDay: "2026-11-03",
          races: 470,
          counted: 0,
          official: 0,
          measures: 142,
          states: 50,
          recordAsOf: null,
          mapAsOf: null,
          hasMap: false,
          stillCounting: false,
          accountability: null,
        },
      ],
    } as ArchiveIndex;
    render(<ArchiveLead season={SEASON} archive={archive} />);
    expect(
      screen.getByText("470 federal races on the record; no state's count was read")
    ).toBeTruthy();
    expect(screen.queryByText(/^0 of 0/)).toBeNull();
  });
});
