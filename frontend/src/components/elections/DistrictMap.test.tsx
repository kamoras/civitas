import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DistrictMap, { fitMercator, leanFill } from "./DistrictMap";
import type { RaceWithCandidates } from "@/types/election";

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
});
