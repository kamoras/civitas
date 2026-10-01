import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DistrictMap, { fitMercator, leanFill } from "./DistrictMap";
import type { LiveRaceResult, RaceWithCandidates } from "@/types/election";

// The real vendored file, so a regenerated topology that the component
// can no longer read fails here rather than on the live page.
const CT = JSON.parse(readFileSync(join(__dirname, "../../../public/data/cd/CT.json"), "utf8"));

function race(district: number, pvi: number | null = 0): RaceWithCandidates {
  return {
    id: `2026-HOUSE-CT-${district}`,
    cycleYear: 2026,
    office: "H",
    state: "CT",
    district,
    isSpecial: false,
    pvi,
    pviLevel: "district",
    candidateSource: "confirmed",
    counties: [],
    candidates: [],
  } as unknown as RaceWithCandidates;
}

const RACES = [1, 2, 3, 4, 5].map((d) => race(d, d === 2 ? -3 : -10));

afterEach(() => vi.unstubAllGlobals());

describe("fitMercator", () => {
  it("centres on the bbox and keeps height inside the clamp", () => {
    const fit = fitMercator(CT.bbox, 800);
    expect(fit.center[0]).toBeCloseTo((CT.bbox[0] + CT.bbox[2]) / 2);
    expect(fit.center[1]).toBeGreaterThan(CT.bbox[1]);
    expect(fit.center[1]).toBeLessThan(CT.bbox[3]);
    expect(fit.height).toBeGreaterThanOrEqual(320);
    expect(fit.height).toBeLessThanOrEqual(900);
  });

  it("gives a tall state a tall frame", () => {
    // Illinois-ish: ~6° wide, ~5.5° tall at a high latitude.
    expect(fitMercator([-91.5, 37, -87.5, 42.5], 800).height).toBeGreaterThan(800);
  });
});

describe("leanFill", () => {
  it("follows pviColor's direction and fades toward even", () => {
    expect(leanFill(-12).fill).toBe(leanFill(-2).fill);
    expect(leanFill(12).fill).not.toBe(leanFill(-12).fill);
    expect(leanFill(2).opacity).toBeLessThan(leanFill(20).opacity);
    expect(leanFill(40).opacity).toBe(leanFill(25).opacity);
  });
});

describe("DistrictMap", () => {
  it("renders every district as a pickable shape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    const onPick = vi.fn();
    render(<DistrictMap state="CT" races={RACES} picked={null} onPick={onPick} />);

    const shape = await screen.findByRole("button", { name: "CT-3" });
    expect(screen.getAllByRole("button")).toHaveLength(5);
    await userEvent.click(shape);
    expect(onPick).toHaveBeenCalledWith("2026-HOUSE-CT-3");
  });

  it("previews a district on keyboard focus and picks on Enter", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    const onPick = vi.fn();
    render(<DistrictMap state="CT" races={RACES} picked={null} onPick={onPick} />);

    const shape = await screen.findByRole("button", { name: "CT-2" });
    shape.focus();
    expect(await screen.findByText(/click to show this race/)).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith("2026-HOUSE-CT-2");
  });

  it("draws nothing for an at-large state and never fetches", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const { container } = render(
      <DistrictMap state="WY" races={[race(0)]} picked={null} onPick={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("leaves the page intact when the geometry cannot load", async () => {
    const fetchSpy = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    vi.stubGlobal("fetch", fetchSpy);
    const { container } = render(
      <DistrictMap state="CT" races={RACES} picked={null} onPick={vi.fn()} />
    );
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("keys every results fill, and never names a tied candidate as ahead", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    const tie = {
      raceId: "2026-HOUSE-CT-2",
      state: "CT",
      office: "H",
      district: 2,
      isSpecial: false,
      heldBy: "DEM",
      official: false,
      votesCounted: 2000,
      reportingUnits: 90,
      totalUnits: 100,
      unitLabel: "towns",
      sourceName: "CT SOTS",
      sourceUrl: null,
      fetchedAt: "2026-11-04T02:44:00Z",
      lastChangeAt: "2026-11-04T02:42:00Z",
      leaderParty: null,
      flip: false,
      candidates: [
        { name: "Ann Rep", party: "REP", votes: 1000, pct: 50, candidateId: null },
        { name: "Bo Dem", party: "DEM", votes: 1000, pct: 50, candidateId: null },
      ],
    } as LiveRaceResult;
    render(
      <DistrictMap
        state="CT"
        races={RACES}
        picked={null}
        onPick={vi.fn()}
        results={new Map([[2, tie]])}
      />
    );
    // Its standing is in its accessible name, not in its colour alone.
    const shape = await screen.findByRole("button", { name: "CT-2: tied, 90% in" });
    expect(screen.getByRole("button", { name: "CT-3: no count shown here" })).toBeTruthy();
    expect(screen.getByText("no votes yet")).toBeInTheDocument();
    // Share is drawn as opacity over a near-black page: less in reads
    // dimmer, not lighter, so the key says "fainter", never "paler".
    expect(
      screen.getByText(
        "fainter = under half in · solid = count listed as official, still not called"
      )
    ).toBeInTheDocument();
    expect(screen.queryByText(/paler/)).not.toBeInTheDocument();
    shape.focus();
    expect(await screen.findByText("tied · not called")).toBeInTheDocument();
    expect(screen.getByText("TIED")).toBeInTheDocument();
    expect(screen.getByText(/Ann Rep/)).not.toHaveClass("text-rep-red");
  });

  it("previews a leader with no party given as that, never 'other'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    const noParty = {
      raceId: "2026-HOUSE-CT-2",
      state: "CT",
      office: "H",
      district: 2,
      isSpecial: false,
      heldBy: "DEM",
      official: false,
      votesCounted: 2000,
      reportingUnits: 90,
      totalUnits: 100,
      unitLabel: "towns",
      sourceName: "CT SOTS",
      sourceUrl: null,
      fetchedAt: "2026-11-04T02:44:00Z",
      lastChangeAt: "2026-11-04T02:42:00Z",
      leaderParty: null,
      flip: false,
      candidates: [
        { name: "Indy Pen", party: null, votes: 1100, pct: 55, candidateId: null },
        { name: "Bo Dem", party: "DEM", votes: 900, pct: 45, candidateId: null },
      ],
    } as LiveRaceResult;
    render(
      <DistrictMap
        state="CT"
        races={RACES}
        picked={null}
        onPick={vi.fn()}
        results={new Map([[2, noParty]])}
      />
    );
    const shape = await screen.findByRole("button", {
      name: "CT-2: leader's party not given, 90% in",
    });
    shape.focus();
    expect(await screen.findByText(/Indy Pen \(party not given\) 55\.0%/)).toBeInTheDocument();
    expect(screen.queryByText(/\(other\)/)).not.toBeInTheDocument();
  });

  it("previews an official count as a lead with its flip, and marks a stale count", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    const flip = {
      raceId: "2026-HOUSE-CT-2",
      state: "CT",
      office: "H",
      district: 2,
      isSpecial: false,
      heldBy: "DEM",
      official: true,
      votesCounted: 2000,
      reportingUnits: 100,
      totalUnits: 100,
      unitLabel: "towns",
      sourceName: "CT SOTS",
      sourceUrl: null,
      fetchedAt: "2026-11-04T02:44:00Z",
      lastChangeAt: "2026-11-04T02:42:00Z",
      leaderParty: "REP",
      flip: true,
      candidates: [
        { name: "Ann Rep", party: "REP", votes: 1200, pct: 60, candidateId: null },
        { name: "Bo Dem", party: "DEM", votes: 800, pct: 40, candidateId: null },
      ],
    } as LiveRaceResult;
    const { container } = render(
      <DistrictMap
        state="CT"
        races={RACES}
        picked={null}
        onPick={vi.fn()}
        results={new Map([[2, flip]])}
        stale
      />
    );
    const shape = await screen.findByRole("button", {
      name: "CT-2: Republican leads, official count, seat changing party; not live, the last count read",
    });
    expect(screen.getByText("stale: the last count read, not live")).toBeInTheDocument();
    // Its leader's colour under the stale stripe.
    expect(container.querySelector("pattern[id$='-stale-0'] rect:nth-child(2)")).toHaveAttribute(
      "fill",
      "rgba(255,137,137, 1)"
    );
    shape.focus();
    // Never a bare "official" beside the names, and the flip kept.
    expect(
      await screen.findByText(
        "leads · official count · not called · held by a Democrat, leader from another party · not live"
      )
    ).toBeInTheDocument();
  });

  it("hatches a district with no count shown beside counted ones, and keys purple", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    const ind = {
      raceId: "2026-HOUSE-CT-2",
      state: "CT",
      office: "H",
      district: 2,
      isSpecial: false,
      heldBy: "DEM",
      official: false,
      votesCounted: 2000,
      reportingUnits: 90,
      totalUnits: 100,
      unitLabel: "towns",
      sourceName: "CT SOTS",
      sourceUrl: null,
      fetchedAt: "2026-11-04T02:44:00Z",
      lastChangeAt: "2026-11-04T02:42:00Z",
      leaderParty: "IND",
      flip: false,
      candidates: [
        { name: "Ivy Ind", party: "IND", votes: 1200, pct: 60, candidateId: null },
        { name: "Bo Dem", party: "DEM", votes: 800, pct: 40, candidateId: null },
      ],
    } as LiveRaceResult;
    const { container } = render(
      <DistrictMap
        state="CT"
        races={RACES}
        picked={null}
        onPick={vi.fn()}
        results={new Map([[2, ind]])}
      />
    );
    const other = await screen.findByRole("button", {
      name: "CT-3: no count shown here",
    });
    expect(screen.getByRole("button", { name: "CT-2: independent leads, 90% in" })).toBeTruthy();
    expect(other.getAttribute("style") ?? "").toMatch(/fill: url\("?#tex-[^"]*-nocount/);
    expect(container.querySelector("pattern[id$='-nocount']")).not.toBeNull();
    expect(screen.getByText("no count shown here")).toBeInTheDocument();
    expect(screen.getByText(/purple = other or unstated party leads/)).toBeInTheDocument();
    other.focus();
    await waitFor(() => expect(screen.getAllByText("no count shown here")).toHaveLength(2));
    expect(screen.queryByText("no votes counted yet")).not.toBeInTheDocument();
  });

  it("names a flip announced earlier whose latest count has no votes, before 'no votes'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    const empty = {
      raceId: "2026-HOUSE-CT-2",
      state: "CT",
      office: "H",
      district: 2,
      isSpecial: false,
      heldBy: "DEM",
      official: false,
      votesCounted: 0,
      reportingUnits: 0,
      totalUnits: 100,
      unitLabel: "towns",
      sourceName: "CT SOTS",
      sourceUrl: null,
      fetchedAt: "2026-11-04T02:44:00Z",
      lastChangeAt: "2026-11-04T02:42:00Z",
      leaderParty: null,
      flip: true,
      candidates: [
        { name: "Ray Rep", party: "REP", votes: 0, pct: null, candidateId: null },
        { name: "Bo Dem", party: "DEM", votes: 0, pct: null, candidateId: null },
      ],
    } as LiveRaceResult;
    render(
      <DistrictMap
        state="CT"
        races={RACES}
        picked={null}
        onPick={vi.fn()}
        results={new Map([[2, empty]])}
      />
    );
    const shape = await screen.findByRole("button", {
      name: "CT-2: no votes in the latest count, change of party announced earlier",
    });
    shape.focus();
    expect(
      await screen.findByText("no votes in the latest count · change of party announced earlier")
    ).toBeInTheDocument();
    expect(screen.queryByText("no votes counted yet")).not.toBeInTheDocument();
  });

  it("shades new-map districts by the new lines' own leans", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    // A redrawn state whose new lines have a table on file: the API serves
    // each race the lean of the district on the ballot.
    const races = [1, 2, 3, 4, 5].map((d) => race(d, d === 2 ? -15 : 6));
    render(<DistrictMap state="CT" newLines races={races} picked={null} onPick={vi.fn()} />);

    const shape = await screen.findByRole("button", { name: "CT-2" });
    expect(
      screen.getByText(/the new 2026 districts · redder = safer R · bluer = safer D/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/no per-district lean published/)).not.toBeInTheDocument();
    expect(shape.getAttribute("style")).not.toBe(
      screen.getByRole("button", { name: "CT-4" }).getAttribute("style")
    );
  });

  it("leaves new-map districts unshaded and says why when only the statewide lean is known", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    // A redrawn state with no table for its new lines on file: every race
    // carries the flagged statewide lean.
    const statewide = [1, 2, 3, 4, 5].map((d) => ({ ...race(d, 6), pviLevel: "state" as const }));
    render(<DistrictMap state="CT" newLines races={statewide} picked={null} onPick={vi.fn()} />);

    const shape = await screen.findByRole("button", { name: "CT-2" });
    expect(
      screen.getByText(/the new 2026 districts · no per-district lean published here yet/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/redder = safer R/)).not.toBeInTheDocument();
    const style = shape.getAttribute("style") ?? "";
    // Every seat the same neutral, never the state's R+6 red.
    expect(leanFill(6).fill.toLowerCase()).toBe("#ff8989");
    expect(style).not.toContain("rgb(255, 137, 137)");
    expect(style).toBe(screen.getByRole("button", { name: "CT-4" }).getAttribute("style"));
    // Borders drawn light so thirty-eight same-coloured districts still read.
    expect(style).toContain("stroke: rgb(217, 211, 199)");

    shape.focus();
    expect(await screen.findByText("R+6 (statewide)")).toBeInTheDocument();
  });

  it("keys lean intensity as fainter = closer, never paler", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    render(<DistrictMap state="CT" races={RACES} picked={null} onPick={vi.fn()} />);
    await screen.findByRole("button", { name: "CT-2" });
    expect(
      screen.getByText(/redder = safer R · bluer = safer D · fainter = closer/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/paler/)).not.toBeInTheDocument();
  });

  it("shows no lean from election day, even with no count to shade by", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    render(
      <DistrictMap state="CT" races={RACES} picked={null} onPick={vi.fn()} showLean={false} />
    );
    const two = await screen.findByRole("button", { name: "CT-2" });
    expect(screen.getByText("no lean shown from election day")).toBeInTheDocument();
    expect(screen.queryByText(/redder = safer R/)).not.toBeInTheDocument();
    // CT-2 (D+3) and CT-4 (D+10) are drawn alike: no lean in the fill.
    expect(two.getAttribute("style")).toBe(
      screen.getByRole("button", { name: "CT-4" }).getAttribute("style")
    );
    two.focus();
    expect(await screen.findByText(/click to show this race/)).toBeInTheDocument();
    expect(screen.queryByText("D+3")).not.toBeInTheDocument();
  });

  it("keys a lone statewide-only district as grey among shaded ones", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => CT }));
    const mixed = RACES.map((r) => (r.district === 3 ? { ...r, pviLevel: "state" as const } : r));
    render(<DistrictMap state="CT" races={mixed} picked={null} onPick={vi.fn()} />);

    const three = await screen.findByRole("button", { name: "CT-3" });
    expect(screen.getByText(/redder = safer R .* grey = no district lean yet/)).toBeInTheDocument();
    expect(three.getAttribute("style")).not.toBe(
      screen.getByRole("button", { name: "CT-4" }).getAttribute("style")
    );
  });
});
