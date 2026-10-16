import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import MapAccountability, { statusSentence } from "./MapAccountability";
import { accountabilityCard } from "./AccountabilityCard";
import { ELECTIONS_ARCHIVE_HREF } from "@/lib/routes";
import type {
  AccountabilitySeat,
  ChamberAccountability,
  CoverageCell,
  MapAccountability as Data,
} from "@/types/mapAccountability";
import type { ArchiveIndex } from "@/types/electionArchive";

function seat(over: Partial<AccountabilitySeat>): AccountabilitySeat {
  return {
    id: "2026-SEN-GA",
    chamber: "senate",
    state: "GA",
    stateName: "Georgia",
    district: null,
    special: false,
    mapStatus: "range",
    count: "official",
    dem: "Dana Smith",
    rep: "Ray Jones",
    lean: -3.2,
    leanLabel: "D+3",
    rangeBasis: "polls",
    rangeBasisShort: "from polls",
    range80: [-8, 2],
    range95: [-11, 5],
    range80Label: "D+8 to R+2",
    range95Label: "D+11 to R+5",
    sourceName: "Georgia Secretary of State",
    sourceUrl: "https://sos.example",
    fetchedAt: "2026-11-20T15:00:00Z",
    margin: -4,
    marginLabel: "D+4",
    usualMargin: -6.4,
    usualMarginLabel: "D+6",
    swing: 2.4,
    swingLabel: "R+2",
    swingBin: "R_leans",
    inside80: true,
    inside95: true,
    fromCentre: -1,
    ...over,
  };
}

const EMPTY: CoverageCell = {
  graded: 0,
  held80: 0,
  share80: null,
  interval80: null,
  held95: 0,
  share95: null,
  interval95: null,
};

function chamber(seats: AccountabilitySeat[], all: CoverageCell): ChamberAccountability {
  const certified = seats.filter((s) => s.count === "official" || s.count === "not_two_party");
  return {
    ranged: seats.length,
    certified: certified.length,
    counts: {
      official: seats.filter((s) => s.count === "official").length,
      counting: seats.filter((s) => s.count === "counting").length,
      no_count: seats.filter((s) => s.count === "no_count").length,
      not_two_party: seats.filter((s) => s.count === "not_two_party").length,
    },
    all,
    byBasis: [{ ...all, basis: "polls", label: "from polls", ranged: seats.length }],
    promised: {
      nominal80: 0.8,
      nominal95: 0.95,
      heldOut: {
        cycles: [1998, 2024],
        races: 300,
        coverage80: 0.81,
        coverage95: 0.94,
        coverage80Fundamentals: 0.79,
      },
      forward: { cycle: 2024, races: 34, coverage80: 0.82, coverage95: 0.97 },
    },
    misses: seats.filter((s) => s.inside80 === false).map((s) => ({ ...s, outsideBy: 15 })),
    swing: {
      seats: seats.filter((s) => s.swing != null).length,
      mean: 2.4,
      meanLabel: "R+2",
      median: 2.4,
      medianLabel: "R+2",
    },
    seats,
  };
}

const NC = seat({
  id: "2026-SEN-NC",
  state: "NC",
  stateName: "North Carolina",
  margin: 20,
  marginLabel: "R+20",
  range80Label: "D+5 to R+5",
  range95Label: "D+10 to R+10",
  inside80: false,
  inside95: false,
  swing: 18,
  swingLabel: "R+18",
  swingBin: "R_strongly",
});

function data(over: Partial<Data> = {}): Data {
  return {
    cycle: 2026,
    electionDay: "2026-11-03",
    mapAsOf: "2026-11-02T23:55:00Z",
    units: { lean: "Lean: half a margin.", margin: "Margin: twice a lean." },
    bins: { leansMax: 5, usuallyMax: 15 },
    intervalLevel: 0.95,
    stillCounting: false,
    chambers: {
      senate: chamber([seat({}), NC], {
        graded: 2,
        held80: 1,
        share80: 0.5,
        interval80: [0.013, 0.987],
        held95: 1,
        share95: 0.5,
        interval95: [0.013, 0.987],
      }),
      house: chamber(
        [
          seat({
            id: "2026-HOUSE-GA-6",
            chamber: "house",
            district: 6,
            count: "counting",
            margin: null,
            marginLabel: null,
            swing: null,
            swingLabel: null,
            swingBin: null,
            inside80: null,
            inside95: null,
          }),
        ],
        EMPTY
      ),
    },
    ...over,
  };
}

describe("MapAccountability", () => {
  it("shows the misses, the measured share beside the promise, and the swing", () => {
    render(<MapAccountability data={data()} pageUrl="https://civitas.example/elections/2026" />);
    expect(statusSentence(data())).toBe(
      "2 of 3 seats with a range have an official count. The rest never had an official Democrat-versus-Republican count Civitas could read."
    );
    const [miss] = screen.getAllByRole("link", { name: "NC Senate" });
    expect(miss.getAttribute("href")).toBe("#race-2026-SEN-NC");
    expect(miss.parentElement?.textContent).toContain("R+20, range D+5 to R+5");
    expect(miss.parentElement?.textContent).toContain("outside the 95% range too");
    expect(
      screen.getAllByText(/50% inside \(exact 95% interval 1% to 99%\); promised 80%/)
    ).toHaveLength(1);
    expect(screen.getByText(/the median seat ran R\+2 against its usual vote/)).toBeTruthy();
    // A chamber with nothing official says so, not zeros.
    expect(
      screen.getByText("House: no seat has an official Democrat-versus-Republican count yet.")
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "CSV · SWING AND RANGES" }).getAttribute("href")).toBe(
      "/api/elections/archive/2026/swing.csv"
    );
    // Source lines inside each shareable section.
    for (const id of ["map-check", "swing-map"]) {
      const section = document.querySelector(`[data-share-section="${id}"]`)!;
      expect(section.textContent).toContain("used once the state lists them official");
    }
  });

  it("before any count is official, says so as content", () => {
    const d = data();
    d.chambers.senate = chamber(
      [seat({ count: "counting", margin: null, swing: null, swingBin: null, inside80: null })],
      EMPTY
    );
    d.stillCounting = true;
    expect(statusSentence(d)).toBe(
      "No count is official yet: 0 of 2 seats with a range. Each seat is checked once its state lists the count official; until then nothing is graded."
    );
    render(<MapAccountability data={d} pageUrl="https://civitas.example/elections/2026" />);
    expect(screen.getAllByText("None checked yet.").length).toBeGreaterThan(0);
  });

  it("has no em dashes in what it shows", () => {
    const { container } = render(
      <MapAccountability data={data()} pageUrl="https://civitas.example/elections/2026" />
    );
    expect(container.textContent).not.toContain("—");
  });

  it("has no axe violations", async () => {
    render(
      <main>
        <MapAccountability data={data()} pageUrl="https://civitas.example/elections/2026" />
      </main>
    );
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});

describe("accountabilityCard", () => {
  const index = (over: Partial<ArchiveIndex["cycles"][number]> | null): ArchiveIndex => ({
    cycles: over
      ? [
          {
            cycle: 2026,
            electionDay: "2026-11-03",
            recordAsOf: null,
            mapAsOf: null,
            hasMap: true,
            stillCounting: false,
            races: 470,
            counted: 400,
            official: 390,
            measures: 0,
            states: 50,
            ...over,
          },
        ]
      : [],
    current: { cycle: 2026, electionDay: "2026-11-03", phase: "campaign" },
  });

  it("before any archive, says when the first check comes", () => {
    const card = accountabilityCard(index(null));
    expect(card.href).toBe(ELECTIONS_ARCHIVE_HREF);
    expect(card.detail).toContain("After the 2026 election");
  });

  it("with nothing official, says so", () => {
    const card = accountabilityCard(
      index({
        accountability: {
          ranged: 40,
          certified: 0,
          official: 0,
          graded: 0,
          held80: 0,
          held95: 0,
          swingSeats: 0,
        },
      })
    );
    expect(card.headline).toBe("2026: no count official yet");
    expect(card.href).toBe("/elections/2026#map-check");
  });

  it("states the counts", () => {
    const card = accountabilityCard(
      index({
        stillCounting: true,
        accountability: {
          ranged: 40,
          certified: 31,
          official: 30,
          graded: 30,
          held80: 25,
          held95: 29,
          swingSeats: 300,
        },
      })
    );
    expect(card.headline).toBe("2026: 25 of 30 official margins inside the map's 80% ranges");
    expect(card.detail).toContain("31 of 40 seats with a range have an official count so far");
  });
});
