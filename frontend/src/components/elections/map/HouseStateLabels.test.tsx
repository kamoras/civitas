import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import HouseStateLabels from "./HouseStateLabels";
import { STATE_LABEL_MAX_UNITS, STATE_LABEL_PX, houseLayout, stateLabelUnits } from "@/lib/electionMap";

const seats = ["CA", "OR", "WA", "WY"].flatMap((state, s) =>
  Array.from({ length: [52, 6, 10, 1][s] }, (_, i) => ({ id: `${state}-${i + 1}`, state, district: i + 1 }))
);

describe("stateLabelUnits", () => {
  it("draws the letters at the phone minimum on screen, or not at all", () => {
    // 448 units drawn 608px wide (the map's widest): 13px is 9.58 units.
    expect(stateLabelUnits(608, 448)! * (608 / 448)).toBeCloseTo(STATE_LABEL_PX);
    expect(stateLabelUnits(608, 448)!).toBeLessThanOrEqual(STATE_LABEL_MAX_UNITS);
    // A phone's 343px would need 17 units: too big for the band, so none.
    expect(stateLabelUnits(343, 448)).toBeNull();
    expect(stateLabelUnits(null, 448)).toBeNull();
  });
});

describe("HouseStateLabels", () => {
  afterEach(() => vi.unstubAllGlobals());

  const renderAt = (width: number) => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      }
    );
    vi.spyOn(SVGElement.prototype, "getBoundingClientRect").mockReturnValue({ width } as DOMRect);
    const layout = houseLayout(seats);
    let out!: ReturnType<typeof render>;
    act(() => {
      out = render(
        <svg viewBox={`-2 -2 ${layout.width + 4} ${layout.height + 4}`}>
          <HouseStateLabels blocks={layout.blocks} viewBoxWidth={layout.width + 4} />
        </svg>
      );
    });
    return { texts: [...out.container.querySelectorAll("text")], units: layout.width + 4 };
  };

  it("names every state at 13px where the map is wide enough", () => {
    const { texts, units } = renderAt(1200);
    expect(texts.map((t) => t.textContent).sort()).toEqual(["CA", "OR", "WA", "WY"]);
    for (const t of texts) {
      const px = Number(t.getAttribute("font-size")) * (1200 / units);
      expect(px).toBeGreaterThanOrEqual(STATE_LABEL_PX);
      expect(px).toBeLessThan(STATE_LABEL_PX + 0.1);
      // A halo in the page's colour where a letter meets a party fill.
      expect(t.getAttribute("class")).toContain("[paint-order:stroke]");
    }
  });

  it("draws none where 13px would not fit above the blocks", () => {
    expect(renderAt(80).texts).toEqual([]);
  });
});
