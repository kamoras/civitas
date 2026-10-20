import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import axe from "axe-core";
import ElectionMap from "./ElectionMap";
import ElectionMapMethod from "./ElectionMapMethod";
import {
  HOUSE_GAP,
  HOUSE_TILE,
  houseLayout,
  changedLine,
  leanWindowWords,
  mapCounts,
  mapTitle,
  parseView,
  rangeLabel,
  seatBin,
  VIEW_EXPLAINER,
  VIEW_LABEL,
  VIEW_STAMP,
} from "@/lib/electionMap";
import type {
  ElectionMapData,
  ElectionMapResponse,
  MapSeat,
  RangedSeat,
} from "@/types/electionMap";
import type { MapChamber, MapView } from "@/lib/electionMap";

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
    meanLabel: "D+2",
    range80: [-8.7, 5.3],
    range80Label: "D+9 to R+5",
    close: true,
    pollSource: { state: "ok", revisionDate: "2026-10-15" },
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
    rangeBasis: "polls",
    rangeBasisLabel: "Margin range, from polls",
    rangeBasisShort: "from polls",
    article: { title: "x", revid: 7, url: "https://en.wikipedia.org/w/index.php?oldid=7" },
    ...over,
  }) as MapSeat;

const data: ElectionMapData = {
  pollWindowDays: 21,
  pollsInRange: true,
  asOf: "2026-10-15T16:00:00Z",
  electionDay: "2026-11-03",
  cycle: 2026,
  daysToElection: 19,
  horizon: 14,
  chamber: "senate",
  national: {
    margin: -8.2,
    label: "D+8",
    shift: -7.2,
    shiftLabel: "D+7",
    updated: "2026-10-14",
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
    coverage80Fundamentals: 0.83,
    meanAbsError: 6.59,
    table: [
      {
        low: 0.5,
        high: 0.65,
        label: "50–65% (near even)",
        n: 42,
        meanChance: 0.571,
        leanShare: 0.571,
        leanCarried: 24,
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
    forward: {
      cycle: 2024,
      trainedOn: [1998, 2022],
      races: 32,
      polledRaces: 30,
      coverage80: 0.938,
      coverage95: 1,
      coverage80Band: [0.656, 0.938],
      meanAbsError: 4.1,
      brier: { combined: 0.052, fundamentals: 0.112, pollsOnly: 0.05 },
      logLoss: 0.175,
    },
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
    training: "MEDSL; FEC; Silver Bulletin",
    trainingPolls: {
      attribution: "Silver Bulletin",
      url: "https://www.natesilver.net/p/pollster-ratings-silver-bulletin",
      terms:
        "You are welcome to use this data free of charge for any purpose, but please attribute it to Silver Bulletin.",
    },
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

const houseSeats: MapSeat[] = [
  ranged({
    id: "2026-HOUSE-OH-9",
    district: 9,
    lean: 2.1,
    leanLabel: "R+2",
    usualBin: "R_leans",
  }),
  ranged({
    id: "2026-HOUSE-OH-1",
    district: 1,
    lean: -2.0,
    leanLabel: "D+2",
    usualBin: "D_leans",
    difference: { strength: 0, direction: null, seatPolls: false },
  }),
  { ...data.seats[1], id: "2026-HOUSE-WY-0", district: 0 } as MapSeat,
];

/** The API's response around the fixture: Senate as `data`, a small House. */
const response: ElectionMapResponse = {
  schemaVersion: 2,
  asOf: data.asOf,
  electionDay: data.electionDay,
  cycle: data.cycle,
  daysToElection: data.daysToElection,
  horizon: data.horizon,
  pollWindowDays: 21,
  pollsInRange: true,
  national: { ...data.national },
  bins: data.bins,
  sources: {
    lean: {
      senate: data.sources.lean,
      house: {
        source: "precinct returns",
        window: "2020+2024",
        file: "app/data/district_lean.json",
      },
    },
    training: { senate: data.sources.training, house: "FEC; VEST; Silver Bulletin" },
    trainingPolls: data.sources.trainingPolls,
    polls: data.sources.polls,
  },
  chambers: {
    senate: {
      shift: -7.2,
      shiftLabel: "D+7",
      calibration: data.calibration,
      model: data.model,
      seats: data.seats,
    },
    house: {
      shift: -6.1,
      shiftLabel: "D+6",
      calibration: { ...data.calibration, cycles: [2018, 2022], races: 1150 },
      model: { ...data.model, file: "app/data/forecast_house_model.json" },
      seats: houseSeats,
    },
  },
};

function renderMap(
  view: MapView,
  chamber: MapChamber = "senate",
  over: {
    seats?: MapSeat[];
    stale?: boolean;
    nationalStatus?: string;
    seat?: string;
    pollsInRange?: boolean;
  } = {}
) {
  const r: ElectionMapResponse = {
    ...response,
    stale: over.stale,
    pollsInRange: over.pollsInRange ?? true,
    national: { ...response.national, status: over.nationalStatus ?? "ok" },
    chambers: over.seats
      ? { ...response.chambers, senate: { ...response.chambers.senate, seats: over.seats } }
      : response.chambers,
  };
  return render(
    <main>
      <ElectionMap
        response={r}
        initialView={view}
        initialChamber={chamber}
        initialSeat={over.seat ?? null}
        pageUrl="https://example.org/elections/map"
      />
    </main>
  );
}

const WIN_WORDS =
  /\b(wins?|winner|winning|favou?red|projected|projection|forecast|prediction|odds of)\b/i;

describe("election map", () => {
  it("opens in the view the link names, with the stamp inside the frame", () => {
    renderMap("polls");
    expect(screen.getByRole("button", { name: /POLLS/ })).toHaveAttribute("aria-pressed", "true");
    const frame = document.querySelector("[data-share-section='general-map']") as HTMLElement;
    expect(within(frame).getByText(`${VIEW_STAMP.polls} · SENATE`)).toBeInTheDocument();
    // The capture carries the licence, the as-of date and "not a call".
    expect(frame.textContent).toContain("Wikipedia contributors, CC BY-SA 4.0");
    expect(frame.textContent).toContain("As of 15 Oct 2026");
    expect(frame.textContent).toContain("Not a race call.");
  });

  it("switches views in place, and a seat opens its range and sources", () => {
    renderMap("usual");
    fireEvent.click(screen.getByRole("button", { name: /DIFFERENCE/ }));
    expect(window.location.search).toContain("view=diff");
    fireEvent.click(screen.getByRole("button", { name: /^Ohio:/ }));
    const detail = screen.getByRole("complementary", { name: "Seat detail" });
    expect(detail.textContent).toContain("margin D+9 to R+5");
    expect(detail.textContent).toContain("TOO CLOSE TO TELL");
    expect(detail.textContent).toContain("Ranges like these held the result 79% of the time");
    expect(within(detail).getByRole("link", { name: "Example Polling" })).toHaveAttribute(
      "href",
      "https://example.org/poll"
    );
  });

  it("further out than the poll window, shows fundamentals-only ranges with polls beside them", () => {
    const far = ranged({
      fundamentalsOnly: true,
      mean: 14.6,
      pollAverage: null,
      pollShare: 0,
      polls: {
        count: 1,
        used: 0,
        latest: null,
        list: [{ ...(ranged() as RangedSeat).polls.list[0], usedInAverage: false }],
      },
      difference: { strength: 0, direction: null, seatPolls: false },
    } as Partial<RangedSeat>);
    renderMap("polls", "senate", { pollsInRange: false, seats: [far, data.seats[1]] });
    const frame = document.querySelector("[data-share-section='general-map']") as HTMLElement;
    expect(frame.textContent).toContain("FUNDAMENTALS ONLY · 19 DAYS OUT.");
    expect(frame.textContent).toContain("checked only over the final 21 days before election day");
    expect(frame.textContent).toContain("Past polls the ranges are fitted on: Silver Bulletin.");
    fireEvent.click(screen.getByRole("button", { name: /^Ohio:/ }));
    const detail = screen.getByRole("complementary", { name: "Seat detail" });
    expect(detail.textContent).toContain("Fundamentals only");
    expect(detail.textContent).toContain(
      "Fundamentals-only ranges held the result 83% of the time"
    );
    expect(
      within(detail).getByRole("list", { name: "Newest polls, not in the range" })
    ).toBeInTheDocument();
    expect(detail.textContent).toContain("The newest polls are listed below, outside the range.");
  });

  it("names the past polls' source and the forward check in the method", () => {
    render(<ElectionMapMethod data={data} />);
    expect(screen.getByRole("link", { name: "Silver Bulletin's raw poll file" })).toHaveAttribute(
      "href",
      "https://www.natesilver.net/p/pollster-ratings-silver-bulletin"
    );
    expect(screen.getByText(/The hardest check is the newest cycle, 2024/).textContent).toContain(
      "Its 80% ranges held the result 94% of the time in 32 races"
    );
  });

  it("says why a seat has no range", () => {
    renderMap("polls");
    fireEvent.click(screen.getByRole("button", { name: /^Wyoming:/ }));
    expect(screen.getByText("No Democratic nominee is on the ballot.")).toBeInTheDocument();
  });

  it("never uses win language, on the map or in the method", () => {
    const { container } = render(
      <>
        <ElectionMap
          response={response}
          initialView="polls"
          initialChamber="senate"
          pageUrl="https://example.org/elections/map"
        />
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
    expect(screen.getByText("50–65% (near even)")).toBeInTheDocument();
    expect(screen.getByText("57% (24 of 42)")).toBeInTheDocument();
    expect(screen.getByText(/In 4 of the 13 held-out cycles/)).toBeInTheDocument();
  });

  it("frames the calibration for either party alike, never one side", () => {
    const { container } = render(<ElectionMapMethod data={data} />);
    const table = within(container).getByRole("table");
    expect(table.textContent).toContain("Side it leaned toward carried");
    expect(table.textContent).not.toMatch(/democrat|republican/i);
    expect(container.textContent).not.toMatch(/seats Democrats carried|went to the Democrat/i);
    // A fit written before the bins changed has no table until its refit.
    const old: ElectionMapData = { ...data, calibration: { ...data.calibration, table: null } };
    const { container: c2 } = render(<ElectionMapMethod data={old} />);
    expect(within(c2).queryByRole("table")).toBeNull();
    // Nor one frozen with the old one-party rows (an archived map).
    const frozen = {
      ...data,
      calibration: {
        ...data.calibration,
        table: [{ low: 0.35, high: 0.65, n: 42, demShare: 0.571 }],
      },
    } as unknown as ElectionMapData;
    const { container: c3 } = render(<ElectionMapMethod data={frozen} />);
    expect(within(c3).queryByRole("table")).toBeNull();
  });

  it("leads with a short summary and keeps the full method behind a disclosure", () => {
    const { container } = render(<ElectionMapMethod data={data} />);
    const summary = screen.getByTestId("method-summary");
    const sentences = (summary.textContent ?? "").split(/(?<=[.:])\s+(?=[A-Z])/).length;
    expect(sentences).toBeLessThanOrEqual(10);
    expect(summary.textContent).toContain("Ranges like these held the result 79% of the time");
    expect(summary.textContent).toContain("These ranges are not a call.");
    expect(summary.textContent).toContain("Nothing here feeds any member");
    expect(summary.textContent).toContain("Wikipedia contributors, CC BY-SA 4.0");
    expect(summary.textContent).toContain("Silver Bulletin");
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details!.open).toBe(false);
    expect(details!.textContent).toContain("Held-out checks");
    expect(details!.textContent).toContain("Inputs, sources and licences");
  });

  it("keeps a range that crosses even neutral, and parses only known views", () => {
    expect(seatBin(data.seats[0], "polls")).toBe("close");
    expect(seatBin(data.seats[1], "polls")).toBeNull();
    expect(rangeLabel(data.seats[0] as RangedSeat)).toBe("D+9 to R+5");
    expect(parseView("diff")).toBe("diff");
    expect(parseView("odds")).toBe("usual");
    const counts = mapCounts(data.seats);
    expect(changedLine("usual", counts, data.national, data.bins)).toBe(
      "0 seats within 5 points of even in the usual vote."
    );
    expect(changedLine("diff", counts, data.national, data.bins)).toBe(
      "1 seat where polls disagree with the usual vote · 1 toward D · 0 toward R"
    );
  });

  it("has no axe violations in any view, with a seat open and the table shown", async () => {
    renderMap("usual");
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

  it("switches to the House: one keyboard stop per state, whose seats open as a list", async () => {
    renderMap("polls");
    fireEvent.click(screen.getByRole("button", { name: "HOUSE" }));
    expect(window.location.search).toContain("chamber=house");
    const frame = document.querySelector("[data-share-section='general-map']") as HTMLElement;
    expect(within(frame).getByText(`${VIEW_STAMP.polls} · HOUSE`)).toBeInTheDocument();
    // The tile map is one stop per state, named with its seat count.
    // The tile map (wider screens; a phone gets the state list, CSS-hidden here).
    const tiles = screen.getByRole("group", { name: "House seats, grouped by state" });
    const ohio = within(tiles).getByRole("button", {
      name: /^Ohio: 2 seats, 2 too close to tell/,
    });
    fireEvent.keyDown(ohio, { key: "Enter" });
    const sheet = screen.getByRole("complementary", { name: "Seat detail" });
    expect(within(sheet).getByText("Ohio · 2 seats")).toBeInTheDocument();
    fireEvent.click(within(sheet).getByRole("button", { name: /Ohio's 9th district/ }));
    expect(sheet.textContent).toContain("Civitas-computed lean, on the 2026 district lines");
    expect(sheet.textContent).toContain("1150 House races, 2018–2022");
    const run = async () =>
      (
        await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } })
      ).violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
    expect(await run()).toEqual([]);
  });

  it("lays the House out as equal tiles, one per seat, blocks never overlapping", () => {
    const seats = Array.from({ length: 52 }, (_, i) =>
      ranged({ id: `CA-${i + 1}`, state: "CA", stateName: "California", district: i + 1 })
    ).concat(
      Array.from({ length: 4 }, (_, i) =>
        ranged({ id: `OR-${i + 1}`, state: "OR", stateName: "Oregon", district: i + 1 })
      )
    );
    const { blocks } = houseLayout(seats);
    expect(blocks.map((b) => b.seats.length).sort((x, y) => x - y)).toEqual([4, 52]);
    const [a, b] = blocks.sort((x, y) => x.y - y.y || x.x - y.x);
    const step = HOUSE_TILE + HOUSE_GAP;
    const apart =
      a.x + a.cols * step <= b.x ||
      b.x + b.cols * step <= a.x ||
      a.y + a.rows * step <= b.y ||
      b.y + b.rows * step <= a.y;
    expect(apart).toBe(true);
    for (const block of blocks)
      expect(block.cols * block.rows).toBeGreaterThanOrEqual(block.seats.length);
  });

  it("offers the House's states as a full-size picker that opens the same list", () => {
    renderMap("usual", "house");
    fireEvent.change(screen.getByLabelText("Pick a state"), { target: { value: "OH" } });
    const sheet = screen.getByRole("complementary", { name: "Seat detail" });
    expect(within(sheet).getByText("Ohio · 2 seats")).toBeInTheDocument();
  });

  it("says when a poll table failed its checks, never that there was no poll", () => {
    const rejected = ranged({
      polls: { count: 0, used: 0, latest: null, list: [] },
      difference: { strength: 0, direction: null, seatPolls: false },
      pollSource: { state: "rejected", detail: "shares sum to 110.0", checkedOn: "2026-10-16" },
    });
    renderMap("polls", "senate", { seats: [rejected] });
    fireEvent.click(screen.getByRole("button", { name: /^Ohio:/ }));
    const detail = screen.getByRole("complementary", { name: "Seat detail" });
    expect(detail.textContent).toContain(
      "failed Civitas's checks on 16 Oct 2026 (shares sum to 110.0)"
    );
    expect(detail.textContent).not.toContain("No poll of this race");
  });

  it("dates a kept national figure and marks a stale map", () => {
    renderMap("polls", "senate", { stale: true, nationalStatus: "kept" });
    expect(screen.getByText(/last good reading, updated 14 Oct 2026/)).toBeInTheDocument();
    expect(screen.getAllByText(/NOT UPDATED SINCE 15 OCT 2026/).length).toBeGreaterThan(0);
  });

  it("opens either race of a two-race state", () => {
    const special = ranged({
      id: "2026-SEN-OH-SPECIAL",
      special: true,
      range80Label: "R+1 to R+9",
      close: false,
    });
    renderMap("polls", "senate", { seats: [ranged(), special] });
    const ohio = screen.getByRole("button", { name: /^Ohio:/ });
    fireEvent.keyDown(ohio, { key: "Enter" });
    const sheet = screen.getByRole("complementary", { name: "Seat detail" });
    fireEvent.click(within(sheet).getByRole("button", { name: /Ohio \(special\)/ }));
    expect(sheet.textContent).toContain("margin R+1 to R+9");
    fireEvent.click(document.querySelector("[data-seat='2026-SEN-OH']") as Element);
    expect(sheet.textContent).toContain("margin D+9 to R+5");
  });

  it("labels a range by what it rests on, never polls on a seat with none", () => {
    const unpolled = ranged({
      difference: { strength: 0, direction: null, seatPolls: false },
      rangeBasis: "no_poll",
      rangeBasisLabel: "Margin range, no poll of this seat",
      rangeBasisShort: "no poll",
    } as Partial<RangedSeat>);
    renderMap("polls", "senate", { seats: [unpolled] });
    const ohio = screen.getByRole("button", { name: /^Ohio:/ });
    expect(ohio.getAttribute("aria-label")).toContain(
      "Margin range, no poll of this seat: D+9 to R+5"
    );
    fireEvent.click(ohio);
    const detail = screen.getByRole("complementary", { name: "Seat detail" });
    expect(detail.textContent).toContain("Margin range, no poll of this seat");
    expect(detail.textContent).toContain("R+5 lean");
    expect(detail.textContent).not.toMatch(/polls suggest|from polls/i);
  });

  it("lists one poll asked two ways once, with both questions", () => {
    const base = (ranged() as RangedSeat).polls.list[0];
    const seat = ranged({
      polls: {
        count: 2,
        used: 1,
        latest: "2026-10-06",
        list: [
          {
            ...base,
            questions: [
              { dem: 40, rep: 49 },
              { dem: 42, rep: 51 },
            ],
          },
        ],
      },
    } as Partial<RangedSeat>);
    renderMap("polls", "senate", { seats: [seat] });
    fireEvent.click(screen.getByRole("button", { name: /^Ohio:/ }));
    const list = screen.getByRole("list", { name: "Polls in the average" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(list.textContent).toContain("D 40 / R 49; D 42 / R 51");
    expect(list.textContent).toContain("one poll asked 2 ways, counted once");
  });

  it("explains the Senate's faint tiles in every view", () => {
    for (const view of ["usual", "polls", "diff"] as const) {
      const { unmount } = renderMap(view, "senate");
      expect(screen.getByText(/no Senate race in this state this year/)).toBeInTheDocument();
      unmount();
    }
  });

  it("draws a seat with no poll of its own with a dashed outline in the Polls view", () => {
    const unpolled = ranged({ difference: { strength: 0, direction: null, seatPolls: false } });
    renderMap("polls", "senate", { seats: [unpolled] });
    expect(
      document.querySelector("[data-seat='2026-SEN-OH']")?.getAttribute("stroke-dasharray")
    ).toBeTruthy();
    expect(
      screen.getByText(/no poll of this seat: the usual vote and the national swing only/)
    ).toBeInTheDocument();
  });

  it("links the race's place on its ballot page, and opens a seat named in the link", () => {
    renderMap("usual", "senate", { seat: "2026-SEN-OH" });
    expect(screen.getByRole("link", { name: /Race page/ })).toHaveAttribute(
      "href",
      "/elections/states/OH#race-2026-SEN-OH"
    );
  });

  it("names each view button by its label alone, thumbnails drawn (wide screen)", () => {
    const original = window.matchMedia;
    window.matchMedia = ((q: string) => ({
      matches: q.includes("min-width"),
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    try {
      renderMap("diff");
      const group = screen.getByRole("group", { name: "Map view" });
      // The Difference thumbnail draws « ‹ glyphs; none reach a name.
      expect(group.querySelector("svg")).not.toBeNull();
      expect(
        within(group)
          .getAllByRole("button")
          .map((b) => b.getAttribute("aria-label"))
      ).toEqual(["USUAL VOTE", "POLLS", "DIFFERENCE"]);
      expect(within(group).getByRole("button", { name: "DIFFERENCE" })).toBeInTheDocument();
    } finally {
      window.matchMedia = original;
    }
  });

  it("is a modal dialog on a phone: Escape closes it and focus goes back", async () => {
    const original = window.matchMedia;
    window.matchMedia = ((q: string) => ({
      matches: q.includes("max-width"),
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    try {
      renderMap("polls");
      const ohio = screen.getByRole("button", { name: /^Ohio:/ });
      ohio.focus();
      fireEvent.click(ohio);
      const dialog = screen.getByRole("dialog", { name: "Seat detail" });
      expect(dialog).toHaveAttribute("aria-modal", "true");
      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await new Promise((r) => requestAnimationFrame(() => r(null)));
      expect(document.activeElement).toBe(ohio);
    } finally {
      window.matchMedia = original;
    }
  });

  it("loads the other chamber only when it is asked for", async () => {
    const senateOnly: ElectionMapResponse = {
      ...response,
      chambers: { senate: response.chambers.senate } as ElectionMapResponse["chambers"],
    };
    const calls: string[] = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (url: string) => {
      calls.push(url);
      return new Response(
        JSON.stringify({ ...response, chambers: { house: response.chambers.house } })
      );
    }) as typeof fetch;
    try {
      render(
        <main>
          <ElectionMap
            response={senateOnly}
            initialView="polls"
            initialChamber="senate"
            pageUrl="https://example.org/elections/map"
          />
        </main>
      );
      expect(calls).toEqual([]);
      fireEvent.click(screen.getByRole("button", { name: "HOUSE" }));
      expect(await screen.findByText(`${VIEW_STAMP.polls} · HOUSE`)).toBeInTheDocument();
      expect(calls).toEqual(["/api/elections/map?chamber=house"]);
    } finally {
      globalThis.fetch = original;
    }
  });
});

describe("labels come from the API's cycle, never a year in the code", () => {
  it("names the map and its lines from the cycle and window", () => {
    expect(mapTitle(2028, "house")).toBe("2028 House map");
    expect(leanWindowWords("2020+2024")).toBe("2020 and 2024");
    expect(leanWindowWords("2024+2028")).toBe("2024 and 2028");
    const later: ElectionMapData = {
      ...data,
      asOf: "2028-10-15T16:00:00Z",
      cycle: 2028,
      chamber: "house",
      sources: { ...data.sources, lean: { ...data.sources.lean, window: "2024+2028" } },
    };
    const { container } = render(<ElectionMapMethod data={later} />);
    const text = container.textContent ?? "";
    expect(text).toContain("2024 and 2028 presidential elections");
    expect(text).toContain("placed on the 2028 district lines");
    expect(text).toContain("Wikipedia's 2028 race articles");
    expect(text).not.toContain("2026");
  });
});
