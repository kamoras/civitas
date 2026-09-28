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
  it("follows pviColor's direction and pales toward even", () => {
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
      <DistrictMap state="WY" races={[race(0)]} picked={null} onPick={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("leaves the page intact when the geometry cannot load", async () => {
    const fetchSpy = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    vi.stubGlobal("fetch", fetchSpy);
    const { container } = render(
      <DistrictMap state="CT" races={RACES} picked={null} onPick={vi.fn()} />,
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
    const shape = await screen.findByRole("button", { name: "CT-2" });
    expect(screen.getByText("no votes yet")).toBeInTheDocument();
    expect(screen.getByText(/solid = official/)).toBeInTheDocument();
    shape.focus();
    expect(await screen.findByText("tied, not called")).toBeInTheDocument();
    expect(screen.getByText("TIED")).toBeInTheDocument();
    expect(screen.getByText(/Ann Rep/)).not.toHaveClass("text-rep-red");
  });

  it("hatches a district the counting state's feed gives no count for, and keys purple", async () => {
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
    const other = await screen.findByRole("button", { name: "CT-3" });
    expect(other.getAttribute("style") ?? "").toMatch(/fill: url\("?#no-count-/);
    expect(container.querySelector("pattern[id^='no-count-']")).not.toBeNull();
    expect(screen.getByText("no count from the state's feed")).toBeInTheDocument();
    expect(screen.getByText(/purple = other party leads/)).toBeInTheDocument();
    other.focus();
    await waitFor(() =>
      expect(screen.getAllByText("no count from the state's feed")).toHaveLength(2)
    );
    expect(screen.queryByText("no votes counted yet")).not.toBeInTheDocument();
  });
});
