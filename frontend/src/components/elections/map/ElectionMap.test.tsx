import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import axe from "axe-core";
import ElectionMap from "./ElectionMap";
import ElectionMapMethod from "./ElectionMapMethod";
import {
  changedLine,
  mapCounts,
  parseView,
  rangeLabel,
  seatBin,
  VIEW_EXPLAINER,
  VIEW_LABEL,
  VIEW_STAMP,
} from "@/lib/electionMap";
import type { ElectionMapData, MapSeat } from "@/types/electionMap";

const ranged = (over: Partial<MapSeat> = {}): MapSeat =>
  ({
    id: "2026-SEN-OH",
    state: "OH",
    stateName: "Ohio",
    special: false,
    lean: 5.2,
    leanLabel: "R+5",
    usualBin: "R_usually",
    dem: "Alex Smith",
    rep: "Jordan Lee",
    incumbentParty: "R",
    status: "range",
    prior: 14.6,
    priorLabel: "R+15",
    mean: -1.7,
    range80: [-8.7, 5.3],
    range95: [-13.0, 9.6],
    pollsBin: "close",
    pollAverage: -4.0,
    pollShare: 0.88,
    polls: {
      count: 3,
      used: 1,
      latest: "2026-10-06",
      list: [
        {
          pollster: "Example Polling",
          start: "2026-10-04",
          end: "2026-10-06",
          sample: 900,
          population: "LV",
          moe: 3.1,
          dem: 47,
          rep: 45,
          partisan: "R",
          url: "https://example.org/poll",
          usedInAverage: true,
        },
      ],
    },
    difference: { strength: 2, direction: "D", seatPolls: true },
    article: { title: "x", revid: 7, url: "https://en.wikipedia.org/w/index.php?oldid=7" },
    ...over,
  }) as MapSeat;

const data: ElectionMapData = {
  schemaVersion: 1,
  asOf: "2026-10-15T16:00:00Z",
  electionDay: "2026-11-03",
  daysToElection: 19,
  horizon: 14,
  chamber: "senate",
  national: {
    margin: -8.2,
    shift: -7.2,
    averages: [],
    article: "a",
    revid: 1,
    url: "https://en.wikipedia.org/w/index.php?oldid=1",
    status: "ok",
  },
  bins: { leansMax: 5, usuallyMax: 15 },
  calibration: {
    cycles: [1998, 2022],
    races: 419,
    coverage80: 0.788,
    coverage95: 0.938,
    meanAbsError: 6.59,
    table: [
      {
        low: 0.35,
        high: 0.65,
        n: 42,
        meanChance: 0.501,
        demShare: 0.571,
        demCarried: 24,
        years: [1998, 2022],
      },
    ],
    seatTotals: Array.from({ length: 13 }, (_, i) => ({
      year: 1998 + 2 * i,
      expectedDem: 16,
      actualDem: 16,
      races: 33,
      independentSd: 1.5,
    })),
    seatTotalsOutside90: 4,
    brier: { combined: 0.056, fundamentals: 0.102, pollsOnly: 0.06, polledRaces: 353 },
  },
  model: {
    beta: [0.2, 1.32, 15.1, 0.87],
    priorSd: 14.4,
    halfLife: 28,
    pollFloor: 4.75,
    pollSpread: 8.5,
    scale: 1.09,
    file: "app/data/forecast_model.json",
    fit: "backend/scripts/fit_forecast_model.py",
  },
  sources: {
    lean: { source: "state returns", window: "2020+2024", file: "app/data/state_pvi.json" },
    training: "MEDSL; FEC; FiveThirtyEight",
    polls: {
      attribution: "Wikipedia contributors",
      license: "CC BY-SA 4.0",
      licenseUrl: "https://creativecommons.org/licenses/by-sa/4.0/",
      notice: "Shared under CC BY-SA 4.0.",
    },
  },
  seats: [
    ranged(),
    {
      id: "2026-SEN-WY",
      state: "WY",
      stateName: "Wyoming",
      special: false,
      lean: 23,
      leanLabel: "R+23",
      usualBin: "R_strongly",
      dem: null,
      rep: "Pat Roe",
      incumbentParty: null,
      status: "not_contested",
      reason: "No Democratic nominee is on the ballot.",
    },
  ],
};

const WIN_WORDS =
  /\b(wins?|winner|winning|favou?red|projected|projection|forecast|prediction|odds of)\b/i;

describe("election map", () => {
  it("opens in the view the link names, with the stamp inside the frame", () => {
    render(<ElectionMap data={data} initialView="polls" />);
    expect(screen.getByRole("button", { name: /POLLS/ })).toHaveAttribute("aria-pressed", "true");
    const frame = document.querySelector("[data-share-section='general-map']") as HTMLElement;
    expect(within(frame).getByText(`${VIEW_STAMP.polls} · SENATE`)).toBeInTheDocument();
    // The capture carries the licence, the as-of date and "not a call".
    expect(frame.textContent).toContain("Wikipedia contributors, CC BY-SA 4.0");
    expect(frame.textContent).toContain("As of 15 Oct 2026");
    expect(frame.textContent).toContain("Not a race call.");
  });

  it("switches views in place, and a seat opens its range and sources", () => {
    render(<ElectionMap data={data} initialView="usual" />);
    fireEvent.click(screen.getByRole("button", { name: /DIFFERENCE/ }));
    expect(window.location.search).toContain("view=diff");
    fireEvent.click(screen.getByRole("button", { name: /^Ohio:/ }));
    const detail = screen.getByRole("complementary", { name: "Seat detail" });
    expect(detail.textContent).toContain("a margin from D+9 to R+5");
    expect(detail.textContent).toContain("TOO CLOSE TO TELL");
    expect(detail.textContent).toContain("Ranges like these held the result 79% of the time");
    expect(within(detail).getByRole("link", { name: "Example Polling" })).toHaveAttribute(
      "href",
      "https://example.org/poll"
    );
  });

  it("says why a seat has no range", () => {
    render(<ElectionMap data={data} initialView="polls" />);
    fireEvent.click(screen.getByRole("button", { name: /^Wyoming:/ }));
    expect(screen.getByText("No Democratic nominee is on the ballot.")).toBeInTheDocument();
  });

  it("never uses win language, on the map or in the method", () => {
    const { container } = render(
      <>
        <ElectionMap data={data} initialView="polls" />
        <ElectionMapMethod data={data} />
      </>
    );
    // "not a prediction" is the one sanctioned use: it says what this is not.
    const text = (container.textContent ?? "").replace(/not a prediction/gi, "");
    expect(text).not.toMatch(WIN_WORDS);
    for (const s of [
      ...Object.values(VIEW_LABEL),
      ...Object.values(VIEW_STAMP),
      ...Object.values(VIEW_EXPLAINER),
    ])
      expect(s).not.toMatch(WIN_WORDS);
  });

  it("shows the held-out calibration exactly as the API gives it", () => {
    render(<ElectionMapMethod data={data} />);
    expect(screen.getByText("35%–65% (near even)")).toBeInTheDocument();
    expect(screen.getByText("57% (24 of 42)")).toBeInTheDocument();
    expect(screen.getByText(/In 4 of the 13 held-out cycles/)).toBeInTheDocument();
  });

  it("keeps a range that crosses even neutral, and parses only known views", () => {
    expect(seatBin(data.seats[0], "polls")).toBe("close");
    expect(seatBin(data.seats[1], "polls")).toBeNull();
    expect(rangeLabel([-8.7, 5.3])).toBe("D+9 to R+5");
    expect(parseView("diff")).toBe("diff");
    expect(parseView("odds")).toBe("usual");
    const counts = mapCounts(data.seats, data.bins);
    expect(changedLine("diff", counts, data.national)).toBe(
      "1 seat where polls disagree with the usual vote · 1 toward D · 0 toward R"
    );
  });

  it("has no axe violations in any view, with a seat open and the table shown", async () => {
    // On the page the map sits inside <main>.
    render(
      <main>
        <ElectionMap data={data} initialView="usual" />
      </main>
    );
    const run = async () =>
      (
        await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } })
      ).violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
    expect(await run()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /POLLS/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Ohio:/ }));
    fireEvent.click(screen.getByRole("button", { name: /TABLE/ }));
    expect(await run()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /DIFFERENCE/ }));
    expect(await run()).toEqual([]);
  });
});
